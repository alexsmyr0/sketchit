/**
 * LobbyClient handles real-time lobby synchronization and host controls.
 */
class LobbyClient {
    constructor() {
        this.joinCode = JSON.parse(document.getElementById('room-join-code').textContent);
        this.currentPlayerId = JSON.parse(document.getElementById('current-player-id').textContent);

        this.elements = {
            roomNameDisplay: document.getElementById('room-name-display'),
            roomStatusBadge: document.getElementById('room-status-badge'),
            participantList: document.getElementById('participant-list'),
            joinUrlInput: document.getElementById('join-url'),
            copyUrlButton: document.getElementById('copy-url-button'),
            settingsForm: document.getElementById('settings-form'),
            editRoomName: document.getElementById('edit-room-name'),
            editVisibility: document.getElementById('edit-visibility'),
            startGameButton: document.getElementById('start-game-button'),
            minPlayersHint: document.getElementById('min-players-hint'),
            hostControls: document.getElementById('host-controls'),
            guestView: document.getElementById('guest-view'),
            guestViewMessage: document.getElementById('guest-view-message'),
            guestViewLoader: document.getElementById('guest-view-loader'),
            lobbyError: document.getElementById('lobby-error'),
            lobbyStatus: document.getElementById('lobby-status'),
        };

        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.baseReconnectDelay = 1000;
        this.maxReconnectDelay = 10000;
        this.statusTimeout = null;
        this.errorTimeout = null;
        this.currentParticipants = [];
        this.currentHostId = null;
        this.currentRoomStatus = null;

        this.init();
    }

    init() {
        this.connectWebSocket();
        this.setupEventListeners();
    }

    setupEventListeners() {
        if (this.elements.copyUrlButton) {
            this.elements.copyUrlButton.addEventListener('click', () => this.copyJoinUrl());
        }

        if (this.elements.settingsForm) {
            this.elements.settingsForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.updateSettings();
            });
        }

        if (this.elements.startGameButton) {
            this.elements.startGameButton.addEventListener('click', () => this.startGame());
        }
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/rooms/${this.joinCode}/`;

        this.socket = new WebSocket(url);

        this.socket.onopen = () => {
            this.reconnectAttempts = 0;
            this.showStatus('Connected to live lobby');
            this.scheduleHideStatus(3000);
        };

        this.socket.onmessage = (event) => {
            this.handleServerEvent(JSON.parse(event.data));
        };

        this.socket.onclose = (event) => {
            const permanentFailures = [4001, 4003, 4004];
            if (permanentFailures.includes(event.code)) {
                this.showError(`Connection rejected: ${event.reason || 'Unauthorized'}`);
                return;
            }

            if (this.reconnectAttempts >= this.maxReconnectAttempts) {
                this.showError('Connection lost. Please refresh the page.');
                return;
            }

            this.reconnectAttempts += 1;
            const delay = Math.min(
                this.maxReconnectDelay,
                this.baseReconnectDelay * Math.pow(2, this.reconnectAttempts - 1),
            );

            this.showStatus(`Connection lost. Retrying in ${Math.round(delay / 1000)}s...`);
            window.setTimeout(() => this.connectWebSocket(), delay);
        };

        this.socket.onerror = () => {
            // onclose handles the retry and user-facing error flow
        };
    }

    handleServerEvent(event) {
        switch (event.type) {
            case 'room.state':
                this.updateLobbyUI(event.payload);
                break;
            case 'host.changed':
                this.handleHostChanged(event.payload);
                break;
            default:
                break;
        }
    }

    handleHostChanged(payload) {
        const previousHostId = this.currentHostId;
        const nextHost = payload && payload.host ? payload.host.id : null;
        this.currentHostId = nextHost;

        if (this.currentParticipants.length > 0) {
            this.renderParticipantList();
        }
        this.syncHostControls();
        this.syncLobbyLockState(this.currentRoomStatus);

        if (previousHostId !== nextHost) {
            this.showStatus('Room host changed');
            this.scheduleHideStatus(3000);
        }
    }

    updateLobbyUI(state) {
        const { room, host, participants } = state;
        const previousHostId = this.currentHostId;
        const previousStatus = this.currentRoomStatus;

        this.currentParticipants = participants;
        this.currentHostId = host ? host.id : null;
        this.currentRoomStatus = room.status;

        if (this.elements.roomNameDisplay) {
            this.elements.roomNameDisplay.textContent = room.name;
        }

        if (this.elements.roomStatusBadge) {
            this.elements.roomStatusBadge.textContent = room.status;
            this.elements.roomStatusBadge.className = `badge ${room.status.toLowerCase()}`;
        }

        this.syncSettingsFormValues(room, {
            force: previousHostId !== this.currentPlayerId && this.isCurrentPlayerHost(),
        });
        this.renderParticipantList();
        this.syncHostControls();
        this.syncLobbyLockState(room.status);

        if (previousHostId !== null && previousHostId !== this.currentHostId) {
            this.showStatus('Room host changed');
            this.scheduleHideStatus(3000);
        } else if (previousStatus === 'lobby' && room.status === 'in_progress') {
            this.showStatus('Game started. Lobby controls are now read-only.');
            this.scheduleHideStatus(3000);
        }
    }

    isCurrentPlayerHost() {
        return this.currentHostId === this.currentPlayerId;
    }

    syncHostControls() {
        const isHost = this.isCurrentPlayerHost();

        if (this.elements.hostControls) {
            this.elements.hostControls.hidden = !isHost;
        }

        if (this.elements.guestView) {
            this.elements.guestView.hidden = isHost;
        }

        this.syncGuestView();

        if (!this.elements.startGameButton) {
            return;
        }

        const eligibleCount = this.currentParticipants.filter((participant) => (
            participant.connection_status === 'CONNECTED'
            && participant.participation_status !== 'SPECTATING'
        )).length;
        const isLobby = this.currentRoomStatus === 'lobby';
        const canStart = isHost && isLobby && eligibleCount >= 2;

        this.elements.startGameButton.disabled = !canStart;

        if (!this.elements.minPlayersHint) {
            return;
        }

        if (!isLobby) {
            this.elements.minPlayersHint.hidden = false;
            this.elements.minPlayersHint.textContent = 'Game already started.';
            return;
        }

        this.elements.minPlayersHint.hidden = eligibleCount >= 2;
        this.elements.minPlayersHint.textContent = eligibleCount >= 2
            ? ''
            : 'Need at least 2 eligible players to start.';
    }

    syncGuestView() {
        if (!this.elements.guestViewMessage
            || !this.elements.guestViewLoader
            || this.isCurrentPlayerHost()
            || this.currentRoomStatus === null) {
            return;
        }

        const isLobby = this.currentRoomStatus === 'lobby';
        this.elements.guestViewMessage.textContent = isLobby
            ? 'Waiting for the host to start the game...'
            : 'Game already started. Lobby settings are read-only.';
        this.elements.guestViewLoader.hidden = !isLobby;
    }

    syncSettingsFormValues(room, { force = false } = {}) {
        if (!this.elements.editRoomName || !this.elements.editVisibility) {
            return;
        }

        const shouldSyncName = force
            || !this.isCurrentPlayerHost()
            || document.activeElement !== this.elements.editRoomName;
        const shouldSyncVisibility = force
            || !this.isCurrentPlayerHost()
            || document.activeElement !== this.elements.editVisibility;

        if (shouldSyncName) {
            this.elements.editRoomName.value = room.name;
        }

        if (shouldSyncVisibility) {
            this.elements.editVisibility.value = room.visibility;
        }
    }

    syncLobbyLockState(roomStatus) {
        if (!this.elements.settingsForm) {
            return;
        }

        const isLobby = roomStatus === 'lobby';
        const canEdit = isLobby && this.isCurrentPlayerHost();
        Array.from(this.elements.settingsForm.elements).forEach((element) => {
            if (element instanceof HTMLButtonElement
                || element instanceof HTMLInputElement
                || element instanceof HTMLSelectElement
                || element instanceof HTMLTextAreaElement) {
                element.disabled = !canEdit;
            }
        });
    }

    renderParticipantList() {
        if (!this.elements.participantList) {
            return;
        }

        this.elements.participantList.innerHTML = '';

        this.currentParticipants.forEach((participant) => {
            const item = document.createElement('li');
            item.className = `participant-item ${participant.id === this.currentPlayerId ? 'is-self' : ''}`;
            item.dataset.playerId = participant.id;

            const status = document.createElement('span');
            status.className = `status-indicator ${participant.connection_status.toLowerCase()}`;
            status.title = participant.connection_status;

            const name = document.createElement('span');
            name.className = 'display-name';
            name.textContent = participant.display_name;

            item.appendChild(status);
            item.appendChild(name);

            if (this.currentHostId && participant.id === this.currentHostId) {
                const hostBadge = document.createElement('span');
                hostBadge.className = 'host-badge';
                hostBadge.textContent = '👑';
                hostBadge.title = 'Room Host';
                item.appendChild(hostBadge);
            }

            if (participant.id === this.currentPlayerId) {
                const selfLabel = document.createElement('span');
                selfLabel.className = 'self-label';
                selfLabel.textContent = '(You)';
                item.appendChild(selfLabel);
            }

            this.elements.participantList.appendChild(item);
        });
    }

    async updateSettings() {
        const name = this.elements.editRoomName.value.trim();
        const visibility = this.elements.editVisibility.value;

        if (!name || name.length > 255) {
            this.showError('Room name must be between 1 and 255 characters.');
            return;
        }

        this.showStatus('Saving settings...');

        try {
            const response = await fetch(`/rooms/${this.joinCode}/settings/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCsrfToken(),
                },
                body: JSON.stringify({ name, visibility }),
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to update settings');
            }

            this.showStatus('Settings saved!');
            this.scheduleHideStatus(3000);
        } catch (error) {
            this.showError(error.message);
        }
    }

    async startGame() {
        this.showStatus('Starting game...');

        try {
            const response = await fetch(`/rooms/${this.joinCode}/start-game/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                },
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to start game');
            }

            this.hideStatus();
        } catch (error) {
            this.showError(error.message);
        }
    }

    async copyJoinUrl() {
        const text = this.elements.joinUrlInput.value;

        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
            } else {
                this.elements.joinUrlInput.select();
                document.execCommand('copy');
            }

            const originalText = this.elements.copyUrlButton.textContent;
            this.elements.copyUrlButton.textContent = '✅';
            window.setTimeout(() => {
                this.elements.copyUrlButton.textContent = originalText;
            }, 2000);
        } catch (error) {
            this.showError('Failed to copy. Please copy manually.');
        }
    }

    getCsrfToken() {
        const csrfField = document.querySelector('[name="csrfmiddlewaretoken"]');
        return csrfField ? csrfField.value : '';
    }

    showError(message) {
        if (!this.elements.lobbyError || !this.elements.lobbyStatus) {
            return;
        }

        if (this.statusTimeout) {
            clearTimeout(this.statusTimeout);
            this.statusTimeout = null;
        }
        if (this.errorTimeout) {
            clearTimeout(this.errorTimeout);
        }

        this.elements.lobbyStatus.hidden = true;
        this.elements.lobbyError.textContent = message;
        this.elements.lobbyError.hidden = false;
        this.errorTimeout = window.setTimeout(() => {
            this.elements.lobbyError.hidden = true;
        }, 5000);
    }

    showStatus(message) {
        if (!this.elements.lobbyError || !this.elements.lobbyStatus) {
            return;
        }

        if (this.statusTimeout) {
            clearTimeout(this.statusTimeout);
        }
        if (this.errorTimeout) {
            clearTimeout(this.errorTimeout);
            this.errorTimeout = null;
        }

        this.elements.lobbyError.hidden = true;
        this.elements.lobbyStatus.textContent = message;
        this.elements.lobbyStatus.hidden = false;
    }

    scheduleHideStatus(delayMs) {
        if (this.statusTimeout) {
            clearTimeout(this.statusTimeout);
        }
        this.statusTimeout = window.setTimeout(() => this.hideStatus(), delayMs);
    }

    hideStatus() {
        if (this.elements.lobbyStatus) {
            this.elements.lobbyStatus.hidden = true;
        }
        if (this.statusTimeout) {
            clearTimeout(this.statusTimeout);
            this.statusTimeout = null;
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.lobbyClient = new LobbyClient();
});
