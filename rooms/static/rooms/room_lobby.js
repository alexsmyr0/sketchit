/**
 * LobbyClient handles both the pre-game lobby and the lightweight gameplay HUD
 * that shares the same room page template.
 */
class LobbyClient {
    constructor() {
        this.joinCode = JSON.parse(document.getElementById('room-join-code').textContent);
        this.currentPlayerId = JSON.parse(document.getElementById('current-player-id').textContent);

        this.elements = {
            lobbyView: document.getElementById('lobby-view'),
            gameView: document.getElementById('game-view'),
            roomNameDisplay: document.getElementById('room-name-display'),
            roomStatusBadge: document.getElementById('room-status-badge'),
            participantList: document.getElementById('participant-list'),
            joinUrlInput: document.getElementById('join-url'),
            copyUrlButton: document.getElementById('copy-url-button'),
            saveSettingsButton: document.getElementById('save-settings-button'),
            settingsForm: document.getElementById('settings-form'),
            editRoomName: document.getElementById('edit-room-name'),
            editVisibility: document.getElementById('edit-visibility'),
            startGameButton: document.getElementById('start-game-button'),
            minPlayersHint: document.getElementById('min-players-hint'),
            hostControls: document.getElementById('host-controls'),
            hostControlsNote: document.getElementById('host-controls-note'),
            guestView: document.getElementById('guest-view'),
            guestViewMessage: document.getElementById('guest-view-message'),
            guestViewLoader: document.getElementById('guest-view-loader'),
            lobbyError: document.getElementById('lobby-error'),
            lobbyStatus: document.getElementById('lobby-status'),

            // Game HUD
            roundNumber: document.getElementById('round-number'),
            timerDisplay: document.getElementById('timer-display'),
            timerBar: document.getElementById('timer-bar'),
            gameParticipantList: document.getElementById('game-participant-list'),
            wordDisplay: document.getElementById('word-display'),
            drawerHint: document.getElementById('drawer-hint'),

            // Guessing
            guessHistory: document.getElementById('guess-history'),
            guessInput: document.getElementById('guess-input'),
            guessInputContainer: document.getElementById('guess-input-container'),
            submitGuessButton: document.getElementById('submit-guess-button'),

            // Intermission
            intermissionOverlay: document.getElementById('intermission-overlay'),
            intermissionTitle: document.getElementById('intermission-title'),
            intermissionResults: document.getElementById('intermission-results'),
            intermissionSeconds: document.getElementById('intermission-seconds'),
            intermissionTimer: document.querySelector('.intermission-timer'),
            intermissionReturnButton: document.getElementById('intermission-return-button'),
        };

        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.baseReconnectDelay = 1000;
        this.maxReconnectDelay = 10000;
        this.statusTimeout = null;
        this.errorTimeout = null;
        this.copyFeedbackTimeout = null;
        this.currentParticipants = [];
        this.currentHostId = this.elements.hostControls && !this.elements.hostControls.hidden
            ? this.currentPlayerId
            : null;
        this.currentRoomStatus = this.elements.roomStatusBadge
            ? this.elements.roomStatusBadge.textContent.trim().toLowerCase()
            : null;
        this.hasReceivedRoomState = false;
        this.isSavingSettings = false;
        this.isStartingGame = false;
        this.isAwaitingStartRoomState = false;
        this.isDrawer = false;
        this.isSpectator = false;
        this.activeRoundId = null;
        this.currentPhase = null;
        this.roundDuration = null;
        this.intermissionDuration = null;

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
            this.elements.settingsForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.updateSettings();
            });
        }

        // Start Game Button
        if (this.elements.startGameButton) {
            this.elements.startGameButton.addEventListener('click', () => this.startGame());
        }

        // Guess Submission
        if (this.elements.guessInput) {
            this.elements.guessInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') this.submitGuess();
            });
        }
        if (this.elements.submitGuessButton) {
            this.elements.submitGuessButton.addEventListener('click', () => this.submitGuess());
        }

        if (this.elements.intermissionReturnButton) {
            this.elements.intermissionReturnButton.addEventListener('click', () => {
                window.location.reload();
            });
        }
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/rooms/${this.joinCode}/`;

        this.socket = new WebSocket(url);

        this.socket.onopen = () => {
            this.reconnectAttempts = 0;
            this.showStatus('Connected to live lobby', { autoHideMs: 3000 });
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
            case 'round.started':
                this.handleRoundStarted(event.payload);
                break;
            case 'round.timer':
                this.handleRoundTimer(event.payload);
                break;
            case 'round.state':
                this.handleRoundState(event.payload);
                break;
            case 'round.intermission_started':
                this.handleIntermissionStarted(event.payload);
                break;
            case 'round.intermission_timer':
                this.handleIntermissionTimer(event.payload);
                break;
            case 'round.drawer_word':
                this.handleDrawerWord(event.payload);
                break;
            case 'guess.result':
                this.handleGuessResult(event.payload);
                break;
            case 'guess.error':
                this.handleGuessError(event.payload);
                break;
            case 'game.finished':
                this.handleGameFinished(event.payload);
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
            this.showStatus('Room host changed', { autoHideMs: 3000 });
        }
    }

    updateLobbyUI(state, { forceSettingsSync = false } = {}) {
        const { room, host, participants } = state;
        const previousHostId = this.currentHostId;
        const previousStatus = this.currentRoomStatus;
        const wasAwaitingStartRoomState = this.isAwaitingStartRoomState;

        this.isAwaitingStartRoomState = false;
        this.hasReceivedRoomState = true;
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
            force: forceSettingsSync || (previousHostId !== this.currentPlayerId && this.isCurrentPlayerHost()),
        });
        this.renderParticipantList();
        this.syncHostControls();
        this.syncLobbyLockState(room.status);
        this.renderGameParticipants();
        this.syncRoomMode();
        this.syncGuessComposer();

        if (previousHostId !== null && previousHostId !== this.currentHostId) {
            this.showStatus('Room host changed', { autoHideMs: 3000 });
        } else if (wasAwaitingStartRoomState && room.status === 'in_progress') {
            this.showStatus('Game started. Lobby controls are now read-only.', { autoHideMs: 3000 });
        } else if (previousStatus === 'lobby' && room.status === 'in_progress') {
            this.showStatus('Game started. Lobby controls are now read-only.', { autoHideMs: 3000 });
        }
    }

    isCurrentPlayerHost() {
        return this.currentHostId === this.currentPlayerId;
    }

    syncHostControls() {
        const isHost = this.isCurrentPlayerHost();
        const isLobby = this.currentRoomStatus === 'lobby';
        const isReadOnly = this.hasReceivedRoomState && !isLobby;

        if (this.elements.hostControls) {
            this.elements.hostControls.hidden = !isHost;
            this.elements.hostControls.dataset.mode = isReadOnly ? 'read-only' : 'editable';
            this.elements.hostControls.dataset.busy = this.isBusy() ? 'true' : 'false';
        }

        if (this.elements.guestView) {
            this.elements.guestView.hidden = isHost;
        }

        this.syncHostControlsNote();
        this.syncGuestView();
        this.syncActionButtons();

        if (!this.elements.startGameButton) {
            return;
        }

        if (!this.hasReceivedRoomState) {
            this.elements.startGameButton.disabled = this.elements.startGameButton.disabled || this.isBusy();
            return;
        }

        const eligibleCount = this.currentParticipants.filter((participant) => (
            participant.connection_status === 'CONNECTED'
            && participant.participation_status !== 'SPECTATING'
        )).length;
        const canStart = isHost && isLobby && eligibleCount >= 2 && !this.isBusy();

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

    syncHostControlsNote() {
        if (!this.elements.hostControlsNote) {
            return;
        }

        const shouldShowReadOnlyNote = this.isCurrentPlayerHost() && this.hasReceivedRoomState && this.currentRoomStatus !== 'lobby';
        this.elements.hostControlsNote.hidden = !shouldShowReadOnlyNote;
        this.elements.hostControlsNote.textContent = shouldShowReadOnlyNote
            ? 'Lobby settings are locked after the game starts.'
            : '';
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

    syncActionButtons() {
        if (this.elements.saveSettingsButton) {
            this.elements.saveSettingsButton.textContent = this.isSavingSettings
                ? 'Saving...'
                : 'Save Settings';
        }

        if (this.elements.startGameButton) {
            this.elements.startGameButton.textContent = this.isStartingGame
                ? 'Starting Game...'
                : 'Start Game';
        }

        if (this.elements.copyUrlButton) {
            this.elements.copyUrlButton.disabled = this.isBusy();
        }
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

        const isLobby = !this.hasReceivedRoomState || roomStatus === 'lobby';
        const canEdit = isLobby && this.isCurrentPlayerHost() && !this.isBusy();
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

    renderGameParticipants() {
        if (!this.elements.gameParticipantList) {
            return;
        }

        this.elements.gameParticipantList.innerHTML = '';

        this.currentParticipants.forEach((participant) => {
            const item = document.createElement('li');
            item.className = `participant-item ${participant.id === this.currentPlayerId ? 'is-self' : ''}`;

            const name = document.createElement('span');
            name.className = 'display-name';
            const score = participant.current_score === undefined ? '—' : participant.current_score;
            const suffix = participant.id === this.currentPlayerId ? ' (You)' : '';
            name.textContent = `${participant.display_name}${suffix}`;

            const scoreNode = document.createElement('span');
            scoreNode.className = 'score-pill';
            scoreNode.textContent = String(score);

            item.appendChild(name);
            item.appendChild(scoreNode);
            this.elements.gameParticipantList.appendChild(item);
        });
    }

    isBusy() {
        return this.isSavingSettings || this.isStartingGame || this.isAwaitingStartRoomState;
    }

    getCurrentParticipant() {
        return this.currentParticipants.find((participant) => participant.id === this.currentPlayerId) || null;
    }

    syncRoomMode() {
        const inGame = this.currentRoomStatus === 'in_progress' || this.currentRoomStatus === 'finished';

        if (this.elements.lobbyView) {
            this.elements.lobbyView.hidden = inGame;
        }
        if (this.elements.gameView) {
            this.elements.gameView.hidden = !inGame;
        }

        if (!inGame) {
            this.resetGameplayStateForLobby();
        }
    }

    resetGameplayStateForLobby() {
        this.currentPhase = null;
        this.activeRoundId = null;
        this.isDrawer = false;
        this.isSpectator = false;
        this.roundDuration = null;
        this.intermissionDuration = null;
        this.hideIntermissionOverlay();

        if (this.elements.wordDisplay) {
            this.elements.wordDisplay.textContent = '_ _ _ _';
        }
        if (this.elements.drawerHint) {
            this.elements.drawerHint.hidden = true;
        }
        if (this.elements.timerDisplay) {
            this.elements.timerDisplay.textContent = '90';
        }
        if (this.elements.timerBar) {
            this.elements.timerBar.style.width = '100%';
        }
        if (this.elements.guessHistory) {
            this.elements.guessHistory.innerHTML = '';
        }
        this.syncGuessComposer();
    }

    syncGuessComposer() {
        if (!this.elements.guessInput || !this.elements.submitGuessButton) {
            return;
        }

        const canSubmit = this.currentRoomStatus === 'in_progress'
            && this.currentPhase === 'round'
            && !this.isDrawer
            && !this.isSpectator
            && this.socket
            && this.socket.readyState === WebSocket.OPEN;

        this.elements.guessInput.disabled = !canSubmit;
        this.elements.submitGuessButton.disabled = !canSubmit;

        if (this.elements.guessInputContainer) {
            this.elements.guessInputContainer.dataset.disabled = canSubmit ? 'false' : 'true';
        }

        if (this.isDrawer) {
            this.elements.guessInput.placeholder = 'You are drawing this round.';
        } else if (this.isSpectator) {
            this.elements.guessInput.placeholder = 'You are spectating this round.';
        } else if (this.currentPhase === 'intermission') {
            this.elements.guessInput.placeholder = 'Wait for the next round...';
        } else if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
            this.elements.guessInput.placeholder = 'Reconnecting...';
        } else {
            this.elements.guessInput.placeholder = 'Type your guess here...';
        }
    }

    setTimerProgress(remainingSeconds) {
        if (this.elements.timerDisplay) {
            this.elements.timerDisplay.textContent = String(Math.max(0, remainingSeconds));
        }

        if (!this.elements.timerBar || !this.roundDuration) {
            return;
        }

        const percent = Math.max(0, Math.min(100, (remainingSeconds / this.roundDuration) * 100));
        this.elements.timerBar.style.width = `${percent}%`;
    }

    setIntermissionSeconds(remainingSeconds) {
        if (this.elements.intermissionSeconds) {
            this.elements.intermissionSeconds.textContent = String(Math.max(0, remainingSeconds));
        }
    }

    setWordDisplay(text) {
        if (this.elements.wordDisplay) {
            this.elements.wordDisplay.textContent = text || '';
        }
    }

    appendGuessHistoryLine(message, outcome = '') {
        if (!this.elements.guessHistory) {
            return;
        }

        const item = document.createElement('li');
        item.className = 'guess-history-item';
        if (outcome) {
            item.dataset.outcome = String(outcome).toLowerCase();
        }
        item.textContent = message;
        this.elements.guessHistory.appendChild(item);
    }

    formatGuessOutcome(payload) {
        const rawOutcome = typeof payload.outcome === 'string' ? payload.outcome.toLowerCase() : 'result';
        const player = this.currentParticipants.find((participant) => participant.id === payload.player_id);
        const actor = player ? player.display_name : 'A player';
        const guessText = payload.text ? ` "${payload.text}"` : '';

        if (typeof payload.message === 'string' && payload.message.trim()) {
            return payload.message;
        }

        switch (rawOutcome) {
            case 'correct':
                return `${actor} guessed correctly${guessText}.`;
            case 'near_match':
                return `${actor} was close${guessText}.`;
            case 'duplicate':
                return `${actor} already tried${guessText}.`;
            case 'incorrect':
                return `${actor} guessed${guessText}.`;
            default:
                return `${actor}: ${rawOutcome}${guessText}`;
        }
    }

    renderLeaderboardRows(leaderboard) {
        if (!Array.isArray(leaderboard) || leaderboard.length === 0) {
            return '<p>No scores yet.</p>';
        }

        return leaderboard.map((participant) => {
            const score = participant.current_score === undefined ? '—' : participant.current_score;
            return `<div class="leaderboard-row"><span>${participant.display_name}</span><span>${score}</span></div>`;
        }).join('');
    }

    showIntermissionOverlay({ title, leaderboard = [], countdownVisible = true }) {
        if (!this.elements.intermissionOverlay) {
            return;
        }

        if (this.elements.intermissionTitle) {
            this.elements.intermissionTitle.textContent = title;
        }
        if (this.elements.intermissionResults) {
            this.elements.intermissionResults.innerHTML = `<h3>Leaderboard</h3>${this.renderLeaderboardRows(leaderboard)}`;
        }
        if (this.elements.intermissionTimer) {
            this.elements.intermissionTimer.hidden = !countdownVisible;
        }
        if (this.elements.intermissionReturnButton) {
            this.elements.intermissionReturnButton.hidden = countdownVisible;
        }

        this.elements.intermissionOverlay.hidden = false;
    }

    hideIntermissionOverlay() {
        if (!this.elements.intermissionOverlay) {
            return;
        }

        this.elements.intermissionOverlay.hidden = true;
        if (this.elements.intermissionTimer) {
            this.elements.intermissionTimer.hidden = false;
        }
        if (this.elements.intermissionReturnButton) {
            this.elements.intermissionReturnButton.hidden = true;
        }
    }

    handleRoundStarted(payload) {
        this.currentPhase = 'round';
        this.activeRoundId = payload.round_id || null;
        this.roundDuration = payload.duration_seconds || this.roundDuration;
        this.isDrawer = payload.role === 'drawer' || payload.drawer_participant_id === this.currentPlayerId;
        this.isSpectator = false;

        if (this.elements.roundNumber && payload.sequence_number) {
            this.elements.roundNumber.textContent = `Round ${payload.sequence_number}`;
        }
        if (this.elements.drawerHint) {
            this.elements.drawerHint.hidden = !this.isDrawer;
        }

        const maskedWord = payload.masked_word ? payload.masked_word.split('').join(' ') : '_ _ _ _';
        this.setWordDisplay(this.isDrawer && payload.word ? payload.word : maskedWord);
        this.setTimerProgress(this.roundDuration || 0);
        this.hideIntermissionOverlay();
        this.syncGuessComposer();
    }

    handleRoundTimer(payload) {
        this.currentPhase = 'round';
        this.activeRoundId = payload.round_id || this.activeRoundId;
        this.setTimerProgress(payload.remaining_seconds || 0);
        this.syncGuessComposer();
    }

    handleRoundState(payload) {
        this.currentPhase = payload.phase || this.currentPhase;
        this.activeRoundId = payload.round_id || this.activeRoundId;
        this.isDrawer = payload.drawer_participant_id === this.currentPlayerId;
        this.isSpectator = this.getCurrentParticipant()?.participation_status === 'SPECTATING';

        if (payload.phase === 'intermission') {
            this.showIntermissionOverlay({
                title: 'Round Over!',
                leaderboard: payload.leaderboard || [],
                countdownVisible: true,
            });
            this.setIntermissionSeconds(payload.remaining_seconds || 0);
        } else {
            this.hideIntermissionOverlay();
        }

        this.syncGuessComposer();
    }

    handleIntermissionStarted(payload) {
        this.currentPhase = 'intermission';
        this.intermissionDuration = payload.duration_seconds || this.intermissionDuration;
        this.setIntermissionSeconds(payload.duration_seconds || 0);
        this.showIntermissionOverlay({
            title: 'Round Over!',
            leaderboard: this.currentParticipants,
            countdownVisible: true,
        });
        this.syncGuessComposer();
    }

    handleIntermissionTimer(payload) {
        this.currentPhase = 'intermission';
        this.setIntermissionSeconds(payload.remaining_seconds || 0);
        this.showIntermissionOverlay({
            title: 'Round Over!',
            leaderboard: this.currentParticipants,
            countdownVisible: true,
        });
        this.syncGuessComposer();
    }

    handleDrawerWord(payload) {
        this.isDrawer = true;
        this.isSpectator = false;
        if (this.elements.drawerHint) {
            this.elements.drawerHint.hidden = false;
        }
        this.setWordDisplay(payload.word || '');
        this.syncGuessComposer();
    }

    handleGuessResult(payload) {
        const message = this.formatGuessOutcome(payload);
        this.appendGuessHistoryLine(message, payload.outcome || '');

        if (payload.player_id === this.currentPlayerId && this.elements.guessInput) {
            this.elements.guessInput.value = '';
        }

        if (Array.isArray(payload.score_updates)) {
            const updatesById = new Map(payload.score_updates.map((update) => [update.player_id, update.current_score]));
            this.currentParticipants = this.currentParticipants.map((participant) => (
                updatesById.has(participant.id)
                    ? { ...participant, current_score: updatesById.get(participant.id) }
                    : participant
            ));
            this.renderGameParticipants();
        }
    }

    handleGuessError(payload) {
        const message = payload && (payload.message || payload.error_message)
            ? (payload.message || payload.error_message)
            : 'Unable to submit that guess right now.';
        this.appendGuessHistoryLine(message, 'error');
        this.showError(message);
        this.syncGuessComposer();
    }

    handleGameFinished(payload) {
        this.currentPhase = 'finished';
        this.activeRoundId = null;
        this.isDrawer = false;
        this.isSpectator = false;

        const winnerHeading = payload.winner
            ? `<p><strong>Winner:</strong> ${payload.winner.display_name}</p>`
            : '<p>No winner recorded.</p>';
        const leaderboardMarkup = this.renderLeaderboardRows(payload.leaderboard || []);

        if (this.elements.intermissionTitle) {
            this.elements.intermissionTitle.textContent = 'Game Over!';
        }
        if (this.elements.intermissionResults) {
            this.elements.intermissionResults.innerHTML = `${winnerHeading}<h3>Final Scores</h3>${leaderboardMarkup}`;
        }
        if (this.elements.intermissionTimer) {
            this.elements.intermissionTimer.hidden = true;
        }
        if (this.elements.intermissionReturnButton) {
            this.elements.intermissionReturnButton.hidden = false;
        }
        if (this.elements.intermissionOverlay) {
            this.elements.intermissionOverlay.hidden = false;
        }

        this.syncGuessComposer();
    }

    submitGuess() {
        if (!this.elements.guessInput || !this.socket || this.socket.readyState !== WebSocket.OPEN) {
            this.showError('Connection is still reconnecting. Please try your guess again in a moment.');
            return;
        }

        if (this.isDrawer || this.isSpectator || this.currentPhase !== 'round') {
            return;
        }

        const text = this.elements.guessInput.value.trim();
        if (!text) {
            this.showError('Guess text cannot be empty.');
            return;
        }

        this.socket.send(JSON.stringify({
            type: 'guess.submit',
            payload: { text },
        }));
    }

    async readResponseData(response) {
        try {
            return await response.json();
        } catch (error) {
            return {};
        }
    }

    getErrorMessage(data, fallbackMessage) {
        if (data && typeof data.detail === 'string') {
            return data.detail;
        }

        if (!data || typeof data.errors !== 'object') {
            return fallbackMessage;
        }

        return Object.entries(data.errors).map(([fieldName, messages]) => {
            if (Array.isArray(messages)) {
                return `${fieldName}: ${messages.join(' ')}`;
            }
            return `${fieldName}: ${String(messages)}`;
        }).join(' ');
    }

    async updateSettings() {
        if (this.isBusy()) {
            return;
        }

        const name = this.elements.editRoomName.value.trim();
        const visibility = this.elements.editVisibility.value;

        if (!name || name.length > 255) {
            this.showError('Room name must be between 1 and 255 characters.');
            return;
        }

        this.isSavingSettings = true;
        this.syncHostControls();
        this.syncLobbyLockState(this.currentRoomStatus);
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

            const data = await this.readResponseData(response);
            if (!response.ok) {
                throw new Error(this.getErrorMessage(data, 'Failed to update settings.'));
            }

            if (data && data.room && Array.isArray(data.participants)) {
                this.updateLobbyUI(data, { forceSettingsSync: true });
            }
            this.showStatus('Settings saved!', { autoHideMs: 3000 });
        } catch (error) {
            this.showError(error.message);
        } finally {
            this.isSavingSettings = false;
            this.syncHostControls();
            this.syncLobbyLockState(this.currentRoomStatus);
        }
    }

    async startGame() {
        if (this.isBusy()) {
            return;
        }

        this.isStartingGame = true;
        this.syncHostControls();
        this.syncLobbyLockState(this.currentRoomStatus);
        this.showStatus('Starting game...');

        try {
            const response = await fetch(`/rooms/${this.joinCode}/start-game/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': this.getCsrfToken(),
                },
            });

            const data = await this.readResponseData(response);
            if (!response.ok) {
                throw new Error(this.getErrorMessage(data, 'Failed to start game.'));
            }

            const responseRoomStatus = (
                data && typeof data.room_status === 'string'
                    ? data.room_status
                    : data && data.room && typeof data.room.status === 'string'
                        ? data.room.status
                        : null
            );
            const shouldAwaitRoomState = this.currentRoomStatus === 'lobby';
            this.isAwaitingStartRoomState = shouldAwaitRoomState;

            if (responseRoomStatus) {
                this.currentRoomStatus = responseRoomStatus;
            }

            if (this.elements.roomStatusBadge && this.currentRoomStatus) {
                this.elements.roomStatusBadge.textContent = this.currentRoomStatus;
                this.elements.roomStatusBadge.className = `badge ${this.currentRoomStatus.toLowerCase()}`;
            }

            this.syncHostControls();
            this.syncLobbyLockState(this.currentRoomStatus);
            this.showStatus(
                shouldAwaitRoomState
                    ? 'Game started. Waiting for live room sync...'
                    : 'Game started. Lobby controls are now read-only.',
                { autoHideMs: 3000 },
            );
        } catch (error) {
            this.showError(error.message);
        } finally {
            this.isStartingGame = false;
            this.syncHostControls();
            this.syncLobbyLockState(this.currentRoomStatus);
        }
    }

    async copyJoinUrl() {
        if (!this.elements.joinUrlInput || !this.elements.copyUrlButton || this.isBusy()) {
            return;
        }

        const text = this.elements.joinUrlInput.value;

        try {
            if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                await navigator.clipboard.writeText(text);
            } else {
                if (typeof this.elements.joinUrlInput.select === 'function') {
                    this.elements.joinUrlInput.select();
                }
                const copied = typeof document.execCommand === 'function' && document.execCommand('copy');
                if (!copied) {
                    throw new Error('Clipboard copy failed');
                }
            }

            this.showStatus('Invite link copied.', { autoHideMs: 2000 });
            this.setCopyButtonFeedback('✅', 2000);
        } catch (error) {
            this.showError('Failed to copy invite link. Please copy it manually.');
        }
    }

    setCopyButtonFeedback(text, resetDelayMs) {
        if (!this.elements.copyUrlButton) {
            return;
        }

        if (this.copyFeedbackTimeout) {
            clearTimeout(this.copyFeedbackTimeout);
            this.copyFeedbackTimeout = null;
        }

        const originalText = '📋';
        this.elements.copyUrlButton.textContent = text;
        this.copyFeedbackTimeout = window.setTimeout(() => {
            this.elements.copyUrlButton.textContent = originalText;
            this.copyFeedbackTimeout = null;
        }, resetDelayMs);
    }

    getCsrfToken() {
        const csrfField = document.querySelector('[name="csrfmiddlewaretoken"]');
        return csrfField ? csrfField.value : '';
    }

    showError(message, { autoHideMs = 5000 } = {}) {
        if (!this.elements.lobbyError || !this.elements.lobbyStatus) {
            return;
        }

        this.hideStatus();
        this.hideError();
        this.elements.lobbyError.textContent = message;
        this.elements.lobbyError.hidden = false;
        if (autoHideMs !== null) {
            this.scheduleHideError(autoHideMs);
        }
    }

    showStatus(message, { autoHideMs = null } = {}) {
        if (!this.elements.lobbyError || !this.elements.lobbyStatus) {
            return;
        }

        this.hideError();
        this.hideStatus();
        this.elements.lobbyStatus.textContent = message;
        this.elements.lobbyStatus.hidden = false;
        if (autoHideMs !== null) {
            this.scheduleHideStatus(autoHideMs);
        }
    }

    scheduleHideStatus(delayMs) {
        if (this.statusTimeout) {
            clearTimeout(this.statusTimeout);
        }
        this.statusTimeout = window.setTimeout(() => this.hideStatus(), delayMs);
    }

    scheduleHideError(delayMs) {
        if (this.errorTimeout) {
            clearTimeout(this.errorTimeout);
        }
        this.errorTimeout = window.setTimeout(() => this.hideError(), delayMs);
    }

    hideStatus() {
        if (this.elements.lobbyStatus) {
            this.elements.lobbyStatus.hidden = true;
            this.elements.lobbyStatus.textContent = '';
        }
        if (this.statusTimeout) {
            clearTimeout(this.statusTimeout);
            this.statusTimeout = null;
        }
    }

    hideError() {
        if (this.elements.lobbyError) {
            this.elements.lobbyError.hidden = true;
            this.elements.lobbyError.textContent = '';
        }
        if (this.errorTimeout) {
            clearTimeout(this.errorTimeout);
            this.errorTimeout = null;
        }
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.lobbyClient = new LobbyClient();
});
