/**
 * RoomClient handles real-time room synchronization, lobby controls, 
 * and gameplay logic.
 */
class RoomClient {
    constructor() {
        this.joinCode = JSON.parse(document.getElementById('room-join-code').textContent);
        this.currentPlayerId = JSON.parse(document.getElementById('current-player-id').textContent);
        
        this.elements = {
            // Views
            lobbyView: document.getElementById('lobby-view'),
            gameView: document.getElementById('game-view'),
            intermissionOverlay: document.getElementById('intermission-overlay'),

            // Lobby Info
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
            intermissionTitle: document.getElementById('intermission-title'),
            intermissionResults: document.getElementById('intermission-results'),
            intermissionSeconds: document.getElementById('intermission-seconds')
        };

        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.currentParticipants = [];
        this.currentHostId = null;
        this.isDrawer = false;
        this.activeRoundId = null;

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

        // Guess Submission
        if (this.elements.guessInput) {
            this.elements.guessInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') this.submitGuess();
            });
        }
        if (this.elements.submitGuessButton) {
            this.elements.submitGuessButton.addEventListener('click', () => this.submitGuess());
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
            this.showStatus('Connected to room');
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
        console.log('Received server event:', event.type, event);
        
        switch (event.type) {
            case 'room.state':
                this.updateRoomState(event.payload);
                break;
            case 'round.started':
                this.handleRoundStarted(event.payload);
                break;
            case 'round.timer':
                this.handleRoundTimer(event.payload);
                break;
            case 'round.intermission_timer':
                this.handleIntermissionTimer(event.payload);
                break;
            case 'guess.result':
                this.handleGuessResult(event.payload);
                break;
            case 'game.finished':
                this.handleGameFinished(event.payload);
                break;
            case 'round.state':
                // Sync catch-up handled in payload
                break;
            default:
                console.log('Unhandled event type:', event.type);
        }
    }

    // --- State Management ---

    updateRoomState(state) {
        const { room, host, participants } = state;
        this.currentParticipants = participants;
        this.currentHostId = host ? host.id : null;

        // Sync view based on room status
        if (room.status === 'in_progress') {
            this.switchToGameView();
        } else {
            this.switchToLobbyView();
        }

        // Update Lobby UI
        if (this.elements.roomNameDisplay) this.elements.roomNameDisplay.textContent = room.name;
        if (this.elements.roomStatusBadge) {
            this.elements.roomStatusBadge.textContent = room.status;
            this.elements.roomStatusBadge.className = `badge ${room.status.toLowerCase()}`;
        }

        this.renderParticipantList();

        // Update Start Game Button state (Host only)
        if (this.elements.startGameButton) {
            const canStart = participants.length >= 2;
            this.elements.startGameButton.disabled = !canStart;
            if (this.elements.minPlayersHint) this.elements.minPlayersHint.hidden = canStart;
        }
    }

    switchToLobbyView() {
        this.elements.lobbyView.hidden = false;
        this.elements.gameView.hidden = true;
        this.elements.intermissionOverlay.hidden = true;
    }

    switchToGameView() {
        this.elements.lobbyView.hidden = true;
        this.elements.gameView.hidden = false;
    }

    // --- Gameplay Handlers ---

    handleRoundStarted(payload) {
        const { round_id, sequence_number, drawer_participant_id, word, masked_word, role } = payload;
        this.activeRoundId = round_id;
        this.isDrawer = (drawer_participant_id === this.currentPlayerId);
        
        this.switchToGameView();
        this.elements.intermissionOverlay.hidden = true;
        
        if (this.elements.roundNumber) {
            this.elements.roundNumber.textContent = `Round ${sequence_number}`;
        }

        // Word Display
        if (this.elements.wordDisplay) {
            this.elements.wordDisplay.textContent = this.isDrawer ? word : masked_word;
        }
        
        // Drawer Hint
        if (this.elements.drawerHint) {
            this.elements.drawerHint.hidden = !this.isDrawer;
        }

        // Guess Input Visibility
        if (this.elements.guessInputContainer) {
            this.elements.guessInputContainer.hidden = this.isDrawer;
        }

        // Clear previous round artifacts
        if (this.elements.guessHistory) this.elements.guessHistory.innerHTML = '';
        this.resetTimer();
        
        this.showStatus(`Round ${sequence_number} Started!`);
        setTimeout(() => this.hideStatus(), 2000);
    }

    handleRoundTimer(payload) {
        const { remaining_seconds, duration_seconds } = payload;
        
        if (this.elements.timerDisplay) {
            this.elements.timerDisplay.textContent = remaining_seconds;
        }

        if (this.elements.timerBar) {
            const percent = (remaining_seconds / duration_seconds) * 100;
            this.elements.timerBar.style.width = `${percent}%`;
            
            // Styling based on urgency
            this.elements.timerBar.classList.toggle('warning', remaining_seconds <= 20 && remaining_seconds > 10);
            this.elements.timerBar.classList.toggle('danger', remaining_seconds <= 10);
        }
    }

    handleIntermissionTimer(payload) {
        const { remaining_seconds, phase } = payload;
        
        this.elements.intermissionOverlay.hidden = false;
        if (this.elements.intermissionSeconds) {
            this.elements.intermissionSeconds.textContent = remaining_seconds;
        }
    }

    handleGuessResult(payload) {
        const { player_nickname, text, is_correct, score_updates } = payload;
        
        // Add to Guess Feed
        if (this.elements.guessHistory) {
            const li = document.createElement('li');
            li.className = `guess-item ${is_correct ? 'correct' : ''}`;
            
            const nick = document.createElement('span');
            nick.className = 'nickname';
            nick.textContent = player_nickname;
            
            const msg = document.createElement('span');
            msg.className = 'text';
            msg.textContent = is_correct ? ' guessed correctly!' : `: ${text}`;
            
            li.appendChild(nick);
            li.appendChild(msg);
            this.elements.guessHistory.prepend(li); // Newest at top
        }

        // Update scores in memory and UI
        if (score_updates) {
            score_updates.forEach(update => {
                const player = this.currentParticipants.find(p => p.id === update.player_id);
                if (player) {
                    player.current_score = update.current_score;
                }
            });
            this.renderParticipantList();
        }
    }

    handleGameFinished(payload) {
        this.showStatus('Game Finished!');
        this.elements.intermissionTitle.textContent = 'Game Over!';
        this.elements.intermissionOverlay.hidden = false;
        // The room.state update following this will eventually return everyone to the lobby
    }

    // --- Actions ---

    submitGuess() {
        if (this.isDrawer) return;

        const text = this.elements.guessInput.value.trim();
        if (!text) return;

        this.socket.send(JSON.stringify({
            type: 'guess.submit',
            payload: {
                text: text,
                round_id: this.activeRoundId
            }
        }));

        this.elements.guessInput.value = '';
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
        } catch (err) {
            this.showError(err.message);
        }
    }

    // --- Helpers ---

    renderParticipantList() {
        const renderTo = (container, mini = false) => {
            if (!container) return;
            container.innerHTML = '';
            
            // Sort by score if in game
            const sorted = [...this.currentParticipants].sort((a, b) => (b.current_score || 0) - (a.current_score || 0));

            sorted.forEach(p => {
                const li = document.createElement('li');
                li.className = `participant-item ${p.id === this.currentPlayerId ? 'is-self' : ''}`;
                li.dataset.playerId = p.id;
                
                const status = document.createElement('span');
                status.className = `status-indicator ${p.connection_status.toLowerCase()}`;
                
                const name = document.createElement('span');
                name.className = 'display-name';
                name.textContent = p.display_name;
                
                li.appendChild(status);
                li.appendChild(name);

                if (!mini) {
                    if (this.currentHostId && p.id === this.currentHostId) {
                        const hostBadge = document.createElement('span');
                        hostBadge.className = 'host-badge';
                        hostBadge.textContent = '👑';
                        li.appendChild(hostBadge);
                    }
                    if (p.id === this.currentPlayerId) {
                        const selfLabel = document.createElement('span');
                        selfLabel.className = 'self-label';
                        selfLabel.textContent = '(You)';
                        li.appendChild(selfLabel);
                    }
                } else {
                    // Show score in mini view
                    const score = document.createElement('span');
                    score.className = 'score-badge';
                    score.textContent = p.current_score || 0;
                    li.appendChild(score);
                }

                container.appendChild(li);
            });
        };

        renderTo(this.elements.participantList);
        renderTo(this.elements.gameParticipantList, true);
    }

    resetTimer() {
        if (this.elements.timerBar) {
            this.elements.timerBar.style.width = '100%';
            this.elements.timerBar.classList.remove('warning', 'danger');
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
        if (this.elements.lobbyError) {
            this.elements.lobbyStatus.hidden = true;
            this.elements.lobbyError.textContent = msg;
            this.elements.lobbyError.hidden = false;
            setTimeout(() => { this.elements.lobbyError.hidden = true; }, 5000);
        }
    }

    showStatus(msg) {
        if (this.elements.lobbyStatus) {
            this.elements.lobbyError.hidden = true;
            this.elements.lobbyStatus.textContent = msg;
            this.elements.lobbyStatus.hidden = false;
        }
    }

    hideStatus() {
        if (this.elements.lobbyStatus) this.elements.lobbyStatus.hidden = true;
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.roomClient = new RoomClient();
});
