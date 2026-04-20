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
            lobbyError: document.getElementById('lobby-error'),
            lobbyStatus: document.getElementById('lobby-status')
        };

        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;

        this.init();
    }

    init() {
        this.connectWebSocket();
        this.setupEventListeners();
    }

    setupEventListeners() {
        // Copy Join URL
        if (this.elements.copyUrlButton) {
            this.elements.copyUrlButton.addEventListener('click', () => this.copyJoinUrl());
        }

        // Host Settings Form
        if (this.elements.settingsForm) {
            this.elements.settingsForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.updateSettings();
            });
        }

        // Start Game Button
        if (this.elements.startGameButton) {
            this.elements.startGameButton.addEventListener('click', () => this.startGame());
        }
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/rooms/${this.joinCode}/`;
        
        console.log(`Connecting to WebSocket: ${url}`);
        this.socket = new WebSocket(url);

        this.socket.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.showStatus('Connected to live lobby');
            setTimeout(() => this.hideStatus(), 3000);
        };

        this.socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleServerEvent(data);
        };

        this.socket.onclose = (e) => {
            console.warn('WebSocket closed', e.code, e.reason);
            if (this.reconnectAttempts < this.maxReconnectAttempts) {
                this.reconnectAttempts++;
                this.showStatus(`Connection lost. Retrying (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
                setTimeout(() => this.connectWebSocket(), 2000);
            } else {
                this.showError('Connection lost. Please refresh the page.');
            }
        };

        this.socket.onerror = (err) => {
            console.error('WebSocket error', err);
        };
    }

    handleServerEvent(event) {
        console.log('Received server event:', event);
        
        switch (event.type) {
            case 'room.state':
                this.updateLobbyUI(event.payload);
                break;
            case 'host.changed':
                // The room.state update usually follows or encompasses this, 
                // but we might want specific feedback here.
                this.showStatus('Room host changed');
                break;
            default:
                console.log('Unhandled event type:', event.type);
        }
    }

    updateLobbyUI(state) {
        const { room, host, participants } = state;

        // Redirect if game has started
        if (room.status === 'in_progress') {
            console.log('Game started! Redirecting...');
            window.location.reload(); // For now, reload will pick up the new state/view
            return;
        }

        // Update Room Info
        if (this.elements.roomNameDisplay) {
            this.elements.roomNameDisplay.textContent = room.name;
        }
        if (this.elements.roomStatusBadge) {
            this.elements.roomStatusBadge.textContent = room.status;
            this.elements.roomStatusBadge.className = `badge ${room.status.toLowerCase()}`;
        }

        // Update Participant List
        if (this.elements.participantList) {
            this.renderParticipantList(participants, host);
        }

        // Update Start Game Button state
        if (this.elements.startGameButton) {
            const canStart = participants.length >= 2;
            this.elements.startGameButton.disabled = !canStart;
            if (this.elements.minPlayersHint) {
                this.elements.minPlayersHint.hidden = canStart;
            }
        }
    }

    renderParticipantList(participants, host) {
        this.elements.participantList.innerHTML = '';
        participants.forEach(p => {
            const li = document.createElement('li');
            li.className = `participant-item ${p.id === this.currentPlayerId ? 'is-self' : ''}`;
            li.dataset.playerId = p.id;
            
            const status = document.createElement('span');
            status.className = `status-indicator ${p.connection_status.toLowerCase()}`;
            status.title = p.connection_status;
            
            const name = document.createElement('span');
            name.className = 'display-name';
            name.textContent = p.display_name;
            
            li.appendChild(status);
            li.appendChild(name);

            if (host && p.id === host.id) {
                const hostBadge = document.createElement('span');
                hostBadge.className = 'host-badge';
                hostBadge.textContent = '👑';
                hostBadge.title = 'Room Host';
                li.appendChild(hostBadge);
            }

            if (p.id === this.currentPlayerId) {
                const selfLabel = document.createElement('span');
                selfLabel.className = 'self-label';
                selfLabel.textContent = '(You)';
                li.appendChild(selfLabel);
            }

            this.elements.participantList.appendChild(li);
        });
    }

    async updateSettings() {
        const name = this.elements.editRoomName.value.trim();
        const visibility = this.elements.editVisibility.value;

        if (!name) {
            this.showError('Room name cannot be empty');
            return;
        }

        this.showStatus('Saving settings...');
        try {
            const response = await fetch(`/rooms/${this.joinCode}/settings/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCsrfToken()
                },
                body: JSON.stringify({ name, visibility })
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to update settings');
            }

            this.showStatus('Settings saved!');
            setTimeout(() => this.hideStatus(), 2000);
        } catch (err) {
            this.showError(err.message);
        }
    }

    async startGame() {
        this.showStatus('Starting game...');
        try {
            const response = await fetch(`/rooms/${this.joinCode}/start/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrfToken()
                }
            });

            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.detail || 'Failed to start game');
            }
            
            // Success broadcast will trigger redirect via handleServerEvent
        } catch (err) {
            this.showError(err.message);
        }
    }

    copyJoinUrl() {
        this.elements.joinUrlInput.select();
        document.execCommand('copy');
        
        const originalText = this.elements.copyUrlButton.textContent;
        this.elements.copyUrlButton.textContent = '✅';
        setTimeout(() => {
            this.elements.copyUrlButton.textContent = originalText;
        }, 2000);
    }

    getCsrfToken() {
        return document.querySelector('[name="csrfmiddlewaretoken"]').value;
    }

    showError(msg) {
        this.elements.lobbyStatus.hidden = true;
        this.elements.lobbyError.textContent = msg;
        this.elements.lobbyError.hidden = false;
        setTimeout(() => { this.elements.lobbyError.hidden = true; }, 5000);
    }

    showStatus(msg) {
        this.elements.lobbyError.hidden = true;
        this.elements.lobbyStatus.textContent = msg;
        this.elements.lobbyStatus.hidden = false;
    }

    hideStatus() {
        this.elements.lobbyStatus.hidden = true;
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.lobbyClient = new LobbyClient();
});
