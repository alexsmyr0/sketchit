// rooms/static/rooms/game.js
// Premium client script for SketchIt gameplay page

(function () {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const socketUrl = `${wsProtocol}://${window.location.host}/ws/rooms/${window.location.pathname.split('/')[2]}/`;
  const socket = new WebSocket(socketUrl);

  // UI elements
  const timerBar = document.getElementById('timer-bar');
  const timerFill = document.createElement('div');
  timerFill.className = 'fill';
  timerBar.appendChild(timerFill);
  const scoreBoard = document.getElementById('score-board');
  const guessInput = document.getElementById('guess-input');
  const guessSubmit = document.getElementById('guess-submit');
  const guessResult = document.getElementById('guess-result');
  const intermissionOverlay = document.getElementById('intermission-overlay');
  const intermissionResults = document.getElementById('intermission-results');

  // State
  let roundDuration = null; // seconds from round.started
  let isSpectator = false;
  let currentPlayerId = JSON.parse(document.getElementById('current-player-id')?.textContent || 'null');

  function updateTimer(remaining) {
    if (!roundDuration) return;
    const percent = Math.max(0, Math.min(100, (remaining / roundDuration) * 100));
    timerFill.style.width = percent + '%';
  }

  function renderScoreBoard(participants) {
    const rows = participants.map(p => {
      const score = p.current_score !== undefined ? p.current_score : '—';
      return `<div>${p.display_name}: ${score}</div>`;
    }).join('');
    scoreBoard.innerHTML = rows;
  }

  function handleGuessResult(payload) {
    const outcome = payload.outcome || '';
    const text = payload.text || '';
    let message = '';
    switch (outcome.toUpperCase()) {
      case 'CORRECT':
        message = `✅ Correct! "${text}"`;
        break;
      case 'NEAR_MATCH':
        message = `🔎 Near match: "${text}"`;
        break;
      case 'INCORRECT':
        message = `❌ Incorrect: "${text}"`;
        break;
      default:
        message = `${outcome}: "${text}"`;
    }
    guessResult.textContent = message;
    // Update scores if provided
    if (payload.score_updates) {
      renderScoreBoard(payload.participants);
    }
  }

  function showIntermission(data) {
    intermissionResults.innerHTML = '';
    if (data.leaderboard) {
      const list = data.leaderboard.map(p => `<div class="leaderboard-row"><span>${p.display_name}</span><span>${p.current_score}</span></div>`).join('');
      intermissionResults.innerHTML = `<h3>Leaderboard</h3>${list}`;
    }
    intermissionOverlay.classList.remove('hidden');
  }

  function hideIntermission() {
    intermissionOverlay.classList.add('hidden');
  }

  function handleGameFinished(data) {
    const payload = data.payload;
    const title = document.getElementById('intermission-title');
    const backBtn = document.getElementById('back-to-lobby-btn');
    
    title.textContent = 'Game Over!';
    if (payload.winner) {
      intermissionResults.innerHTML = `<h2>Winner: ${payload.winner.display_name}</h2>`;
    }
    
    if (payload.leaderboard) {
      const list = payload.leaderboard.map(p => `<div class="leaderboard-row"><span>${p.display_name}</span><span>${p.current_score}</span></div>`).join('');
      intermissionResults.innerHTML += `<h3>Final Scores</h3>${list}`;
    }
    
    backBtn.classList.remove('hidden');
    intermissionOverlay.classList.remove('hidden');
  }

  socket.addEventListener('open', () => {
    console.log('WebSocket connected');
  });

  socket.addEventListener('message', (event) => {
    const data = JSON.parse(event.data);
    const type = data.type;
    if (type === 'room.state') {
      if (!currentPlayerId && data.payload.current_player_id) {
        currentPlayerId = data.payload.current_player_id;
      }
      renderScoreBoard(data.payload.participants);
      maybeHideGuessSection(data.payload.participants);
    } else if (type === 'round.started') {
      roundDuration = data.payload.duration_seconds;
      updateTimer(data.payload.remaining_seconds);
      hideIntermission();
      // Reset drawer word display if we are not the drawer
      document.getElementById('drawer-word-display').classList.add('hidden');
    } else if (type === 'round.timer') {
      updateTimer(data.payload.remaining_seconds);
    } else if (type === 'round.state') {
      if (data.payload.phase === 'intermission') {
        showIntermission(data.payload);
      } else {
        hideIntermission();
      }
    } else if (type === 'guess.result') {
      handleGuessResult(data.payload);
    } else if (type === 'guess.error') {
      guessResult.textContent = `⚠️ ${data.payload.message || data.payload.error_message}`;
    } else if (type === 'round.drawer_word') {
      const display = document.getElementById('drawer-word-display');
      display.textContent = `You are drawing: ${data.payload.word}`;
      display.classList.remove('hidden');
    } else if (type === 'game.finished') {
      handleGameFinished(data);
    } else {
      console.warn('Unhandled event', data);
    }
  });

  socket.addEventListener('close', () => {
    console.log('WebSocket closed');
  });

  // Guess submission handling
  guessSubmit.addEventListener('click', () => {
    if (socket.readyState !== WebSocket.OPEN) return;
    const text = guessInput.value.trim();
    if (!text) return;
    socket.send(JSON.stringify({ type: 'guess.submit', payload: { text } }));
    guessInput.value = '';
  });

  // Spectator handling: hide guess UI if spectator status is true (received via room.state participants)
  function maybeHideGuessSection(participants) {
    const me = participants.find(p => p.id === currentPlayerId);
    if (me && me.participation_status === 'SPECTATING') {
      isSpectator = true;
      guessInput.disabled = true;
      guessSubmit.disabled = true;
      guessInput.placeholder = 'You are spectating this round...';
    } else {
      isSpectator = false;
      guessInput.disabled = false;
      guessSubmit.disabled = false;
      guessInput.placeholder = 'Enter your guess...';
    }
  }

  const backToLobbyBtn = document.getElementById('back-to-lobby-btn');
  if (backToLobbyBtn) {
    backToLobbyBtn.addEventListener('click', () => {
      window.location.reload(); // Simple reload for now, or navigate to lobby URL
    });
  }

  // Initial fetch of participant list handled via room.state payload (already in connect)
})();
