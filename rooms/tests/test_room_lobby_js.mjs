import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";


const ROOM_LOBBY_JS_PATH = path.resolve(
    process.cwd(),
    "rooms/static/rooms/room_lobby.js",
);


class MockElement {
    constructor({ value = "", textContent = "", dataset = {}, hidden = false } = {}) {
        this.value = value;
        this.textContent = textContent;
        this.dataset = { ...dataset };
        this.hidden = hidden;
        this.disabled = false;
        this.listeners = new Map();
        this.children = [];
        this.className = "";
        this.title = "";
        this._innerHTML = "";
        this.style = {};
        this.placeholder = "";
        this.classList = {
            add() {},
            remove() {},
        };
    }

    addEventListener(eventName, listener) {
        this.listeners.set(eventName, listener);
    }

    getListener(eventName) {
        return this.listeners.get(eventName);
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    select() {}

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = value;
        if (value === "") {
            this.children = [];
        }
    }
}


class MockButtonElement extends MockElement {}
class MockInputElement extends MockElement {}
class MockSelectElement extends MockElement {}
class MockTextAreaElement extends MockElement {}


function makeDeferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}


function buildRoomState({
    roomStatus = "lobby",
    hostId = 7,
    hostName = "Host Alex",
    participants = null,
} = {}) {
    const participantList = participants ?? [
        {
            id: 7,
            display_name: "Host Alex",
            connection_status: "connected",
            participation_status: "playing",
        },
        {
            id: 9,
            display_name: "Jamie",
            connection_status: "connected",
            participation_status: "playing",
        },
    ];

    return {
        room: {
            name: "Sketch Room",
            join_code: "ROOM1234",
            visibility: "private",
            status: roomStatus,
        },
        host: {
            id: hostId,
            display_name: hostName,
        },
        participants: participantList,
    };
}


async function loadRoomLobbyScript({
    fetchResponse = async () => ({
        ok: true,
        async json() {
            return {};
        },
    }),
    clipboard = {
        async writeText() {},
    },
    execCommandResult = true,
    currentPlayerId = 7,
    hostControlsInitiallyHidden = false,
    guestViewInitiallyHidden = true,
    roomStatusText = "lobby",
} = {}) {
    const source = await readFile(ROOM_LOBBY_JS_PATH, "utf8");
    const listeners = new Map();
    const fetchCalls = [];
    const socketInstances = [];
    const timers = new Map();
    let nextTimerId = 1;

    const roomJoinCode = new MockElement({ textContent: JSON.stringify("ROOM1234") });
    const currentPlayerIdElement = new MockElement({ textContent: JSON.stringify(currentPlayerId) });
    const csrfInput = new MockInputElement({ value: "csrf-token" });
    const lobbyView = new MockElement({ hidden: false });
    const gameView = new MockElement({ hidden: true });
    const participantList = new MockElement();
    const gameParticipantList = new MockElement();
    const hostControls = new MockElement();
    hostControls.hidden = hostControlsInitiallyHidden;
    const guestView = new MockElement({ hidden: guestViewInitiallyHidden });
    const guestViewMessage = new MockElement();
    const guestViewLoader = new MockElement();
    const saveSettingsButton = new MockButtonElement({ textContent: "Save Settings" });
    const editRoomName = new MockInputElement({ value: "Sketch Room" });
    const editVisibility = new MockSelectElement({ value: "private" });
    const startGameButton = new MockButtonElement({ textContent: "Start Game" });
    const minPlayersHint = new MockElement({ hidden: true });
    const copyUrlButton = new MockButtonElement({ textContent: "[copy]" });
    const joinUrlInput = new MockInputElement({ value: "http://localhost:8000/rooms/join/ROOM1234/" });
    const roomStatusBadge = new MockElement({ textContent: roomStatusText });
    const settingsForm = new MockElement();
    settingsForm.elements = [editRoomName, editVisibility, saveSettingsButton];

    const roundNumber = new MockElement({ textContent: "Round 1" });
    const timerDisplay = new MockElement({ textContent: "90" });
    const timerBar = new MockElement();
    timerBar.style.width = "100%";
    const wordDisplay = new MockElement({ textContent: "_ _ _ _" });
    const drawerHint = new MockElement({ hidden: true });
    const guessHistory = new MockElement();
    const guessInput = new MockInputElement({ value: "" });
    const guessInputContainer = new MockElement();
    const submitGuessButton = new MockButtonElement({ textContent: "Send" });

    const intermissionOverlay = new MockElement({ hidden: true });
    const intermissionTitle = new MockElement({ textContent: "Round Over!" });
    const intermissionResults = new MockElement();
    const intermissionSeconds = new MockElement({ textContent: "10" });
    const intermissionTimer = new MockElement({ hidden: false });
    const intermissionReturnButton = new MockButtonElement({
        hidden: true,
        textContent: "Return to lobby",
    });

    const elementsById = new Map([
        ["lobby-view", lobbyView],
        ["game-view", gameView],
        ["room-name-display", new MockElement({ textContent: "Sketch Room" })],
        ["room-status-badge", roomStatusBadge],
        ["participant-list", participantList],
        ["join-url", joinUrlInput],
        ["copy-url-button", copyUrlButton],
        ["save-settings-button", saveSettingsButton],
        ["settings-form", settingsForm],
        ["edit-room-name", editRoomName],
        ["edit-visibility", editVisibility],
        ["start-game-button", startGameButton],
        ["min-players-hint", minPlayersHint],
        ["host-controls", hostControls],
        ["host-controls-note", new MockElement({ hidden: true })],
        ["guest-view", guestView],
        ["guest-view-message", guestViewMessage],
        ["guest-view-loader", guestViewLoader],
        ["lobby-error", new MockElement({ hidden: true })],
        ["lobby-status", new MockElement({ hidden: true })],
        ["round-number", roundNumber],
        ["timer-display", timerDisplay],
        ["timer-bar", timerBar],
        ["game-participant-list", gameParticipantList],
        ["word-display", wordDisplay],
        ["drawer-hint", drawerHint],
        ["guess-history", guessHistory],
        ["guess-input", guessInput],
        ["guess-input-container", guessInputContainer],
        ["submit-guess-button", submitGuessButton],
        ["intermission-overlay", intermissionOverlay],
        ["intermission-title", intermissionTitle],
        ["intermission-results", intermissionResults],
        ["intermission-seconds", intermissionSeconds],
        ["intermission-return-button", intermissionReturnButton],
        ["room-join-code", roomJoinCode],
        ["current-player-id", currentPlayerIdElement],
    ]);

    class MockWebSocket {
        constructor(url) {
            this.url = url;
            this.sent = [];
            this.readyState = MockWebSocket.OPEN;
            socketInstances.push(this);
        }

        send(payload) {
            this.sent.push(payload);
        }
    }
    MockWebSocket.OPEN = 1;

    const document = {
        activeElement: null,
        addEventListener(eventName, listener) {
            listeners.set(eventName, listener);
        },
        querySelector(selector) {
            if (selector === '[name="csrfmiddlewaretoken"]') {
                return csrfInput;
            }
            if (selector === ".intermission-timer") {
                return intermissionTimer;
            }
            return null;
        },
        getElementById(id) {
            return elementsById.get(id) ?? null;
        },
        createElement(tagName) {
            return new MockElement({ dataset: { tagName } });
        },
        execCommand(command) {
            if (command === "copy") {
                return execCommandResult;
            }
            return false;
        },
    };

    const context = {
        console,
        Error,
        Array,
        Object,
        String,
        Map,
        JSON,
        Promise,
        URL,
        document,
        navigator: clipboard ? { clipboard } : {},
        fetch: async (url, options) => {
            fetchCalls.push({ url, options });
            return fetchResponse(url, options);
        },
        WebSocket: MockWebSocket,
        HTMLButtonElement: MockButtonElement,
        HTMLInputElement: MockInputElement,
        HTMLSelectElement: MockSelectElement,
        HTMLTextAreaElement: MockTextAreaElement,
        setTimeout(callback) {
            const timerId = nextTimerId;
            nextTimerId += 1;
            timers.set(timerId, callback);
            return timerId;
        },
        clearTimeout(timerId) {
            timers.delete(timerId);
        },
        window: {
            location: {
                protocol: "http:",
                host: "localhost:8000",
                reloadCalled: false,
                reload() {
                    this.reloadCalled = true;
                },
            },
            setTimeout(callback) {
                const timerId = nextTimerId;
                nextTimerId += 1;
                timers.set(timerId, callback);
                return timerId;
            },
            clearTimeout(timerId) {
                timers.delete(timerId);
            },
        },
    };
    context.window.document = document;
    context.window.navigator = context.navigator;
    context.window.fetch = context.fetch;
    context.window.WebSocket = MockWebSocket;
    context.globalThis = context;

    vm.runInNewContext(source, context, { filename: ROOM_LOBBY_JS_PATH });
    listeners.get("DOMContentLoaded")();

    return {
        client: context.window.lobbyClient,
        elementsById,
        fetchCalls,
        socketInstances,
        intermissionTimer,
        windowLocation: context.window.location,
        runAllTimers() {
            const pending = Array.from(timers.values());
            timers.clear();
            pending.forEach((callback) => callback());
        },
    };
}


test("copy fallback shows an error when execCommand fails", async () => {
    const harness = await loadRoomLobbyScript({
        clipboard: null,
        execCommandResult: false,
    });

    await harness.client.copyJoinUrl();

    assert.equal(harness.elementsById.get("lobby-error").hidden, false);
    assert.equal(
        harness.elementsById.get("lobby-error").textContent,
        "Failed to copy invite link. Please copy it manually.",
    );
    assert.equal(harness.elementsById.get("copy-url-button").textContent, "[copy]");
});


test("updateSettings ignored duplicate submits while the save request was in flight", async () => {
    const deferredResponse = makeDeferred();
    const harness = await loadRoomLobbyScript({
        fetchResponse: async () => deferredResponse.promise,
    });
    harness.client.updateLobbyUI(buildRoomState());
    harness.elementsById.get("edit-room-name").value = "Renamed Room";
    harness.elementsById.get("edit-visibility").value = "public";

    const firstRequest = harness.client.updateSettings();
    const secondRequest = harness.client.updateSettings();

    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.elementsById.get("save-settings-button").disabled, true);
    assert.equal(harness.elementsById.get("save-settings-button").textContent, "Saving...");
    assert.equal(harness.elementsById.get("copy-url-button").disabled, true);
    assert.equal(harness.elementsById.get("lobby-status").textContent, "Saving settings...");

    deferredResponse.resolve({
        ok: true,
        async json() {
            return {};
        },
    });

    await firstRequest;
    await secondRequest;

    assert.equal(harness.elementsById.get("save-settings-button").disabled, false);
    assert.equal(harness.elementsById.get("save-settings-button").textContent, "Save Settings");
    assert.equal(harness.elementsById.get("copy-url-button").disabled, false);
    assert.equal(harness.elementsById.get("lobby-status").hidden, false);
    assert.equal(harness.elementsById.get("lobby-status").textContent, "Settings saved!");
});


test("updateSettings applied the successful response without waiting for websocket sync", async () => {
    const responsePayload = {
        ...buildRoomState(),
        room: {
            ...buildRoomState().room,
            name: "Renamed From Server",
            visibility: "public",
        },
    };
    const harness = await loadRoomLobbyScript({
        fetchResponse: async () => ({
            ok: true,
            async json() {
                return responsePayload;
            },
        }),
    });
    harness.client.updateLobbyUI(buildRoomState());
    harness.elementsById.get("edit-room-name").value = "Renamed From Server";
    harness.elementsById.get("edit-visibility").value = "public";

    harness.fetchCalls.length = 0;

    await harness.client.updateSettings();

    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.elementsById.get("room-name-display").textContent, "Renamed From Server");
    assert.equal(harness.elementsById.get("edit-room-name").value, "Renamed From Server");
    assert.equal(harness.elementsById.get("edit-visibility").value, "public");
    assert.equal(harness.elementsById.get("lobby-status").textContent, "Settings saved!");
});


test("startGame ignored duplicate clicks and left the host in read-only mode after the game started", async () => {
    const deferredResponse = makeDeferred();
    const harness = await loadRoomLobbyScript({
        fetchResponse: async () => deferredResponse.promise,
    });
    harness.client.updateLobbyUI(buildRoomState());

    const firstRequest = harness.client.startGame();
    const secondRequest = harness.client.startGame();

    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.elementsById.get("start-game-button").disabled, true);
    assert.equal(harness.elementsById.get("start-game-button").textContent, "Starting Game...");
    assert.equal(harness.elementsById.get("edit-room-name").disabled, true);
    assert.equal(harness.elementsById.get("copy-url-button").disabled, true);

    harness.client.updateLobbyUI(buildRoomState({ roomStatus: "in_progress" }));

    deferredResponse.resolve({
        ok: true,
        async json() {
            return {};
        },
    });

    await firstRequest;
    await secondRequest;

    assert.equal(harness.elementsById.get("start-game-button").textContent, "Start Game");
    assert.equal(harness.elementsById.get("start-game-button").disabled, true);
    assert.equal(harness.elementsById.get("edit-room-name").disabled, true);
    assert.equal(harness.elementsById.get("host-controls-note").hidden, false);
    assert.equal(
        harness.elementsById.get("host-controls-note").textContent,
        "Lobby settings are locked after the game starts.",
    );
    assert.equal(harness.elementsById.get("min-players-hint").hidden, false);
    assert.equal(harness.elementsById.get("min-players-hint").textContent, "Game already started.");
    assert.equal(harness.elementsById.get("copy-url-button").disabled, false);
    assert.equal(
        harness.elementsById.get("lobby-status").textContent,
        "Game started. Lobby controls are now read-only.",
    );
});


test("startGame success kept the lobby locked until the next room.state arrived", async () => {
    const harness = await loadRoomLobbyScript({
        fetchResponse: async () => ({
            ok: true,
            async json() {
                return {
                    room_status: "in_progress",
                    room: {
                        status: "in_progress",
                    },
                };
            },
        }),
    });
    harness.client.updateLobbyUI(buildRoomState());

    await harness.client.startGame();
    await harness.client.startGame();

    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.elementsById.get("start-game-button").disabled, true);
    assert.equal(harness.elementsById.get("edit-room-name").disabled, true);
    assert.equal(harness.elementsById.get("copy-url-button").disabled, true);
    assert.equal(harness.elementsById.get("room-status-badge").textContent, "in_progress");
    assert.equal(harness.elementsById.get("host-controls-note").hidden, false);
    assert.equal(
        harness.elementsById.get("lobby-status").textContent,
        "Game started. Waiting for live room sync...",
    );

    harness.client.updateLobbyUI(buildRoomState({ roomStatus: "in_progress" }));

    assert.equal(harness.elementsById.get("copy-url-button").disabled, false);
    assert.equal(
        harness.elementsById.get("lobby-status").textContent,
        "Game started. Lobby controls are now read-only.",
    );
});


test("guest promotion via host.changed exposed host controls before the next room.state", async () => {
    const harness = await loadRoomLobbyScript({
        currentPlayerId: 9,
        hostControlsInitiallyHidden: true,
        guestViewInitiallyHidden: false,
    });
    harness.client.updateLobbyUI(buildRoomState());

    harness.client.handleServerEvent({
        type: "host.changed",
        payload: {
            host: {
                id: 9,
                display_name: "Jamie",
            },
        },
    });

    assert.equal(harness.elementsById.get("host-controls").hidden, false);
    assert.equal(harness.elementsById.get("guest-view").hidden, true);
    assert.equal(harness.elementsById.get("start-game-button").disabled, false);
    assert.equal(harness.elementsById.get("edit-room-name").disabled, false);
    assert.equal(harness.elementsById.get("lobby-status").textContent, "Room host changed");

    const participantEntries = harness.elementsById.get("participant-list").children;
    assert.equal(participantEntries.length, 2);
    assert.equal(participantEntries[1].children[2].title, "Room Host");
});


test("room.state after host promotion kept the new host editable and moved the crown", async () => {
    const harness = await loadRoomLobbyScript({
        currentPlayerId: 9,
        hostControlsInitiallyHidden: true,
        guestViewInitiallyHidden: false,
    });
    harness.client.updateLobbyUI(buildRoomState());

    harness.client.handleServerEvent({
        type: "host.changed",
        payload: {
            host: {
                id: 9,
                display_name: "Jamie",
            },
        },
    });
    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({
            hostId: 9,
            hostName: "Jamie",
        }),
    });

    assert.equal(harness.elementsById.get("host-controls").hidden, false);
    assert.equal(harness.elementsById.get("guest-view").hidden, true);
    assert.equal(harness.elementsById.get("start-game-button").disabled, false);
    assert.equal(harness.elementsById.get("edit-room-name").disabled, false);

    const participantEntries = harness.elementsById.get("participant-list").children;
    assert.equal(participantEntries.length, 2);
    assert.equal(participantEntries[0].children.length, 2);
    assert.equal(participantEntries[1].children[2].title, "Room Host");
    assert.equal(participantEntries[1].children[3].textContent, "(You)");
});


test("in-progress room.state switches to the gameplay view and renders scores", async () => {
    const harness = await loadRoomLobbyScript();

    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({
            roomStatus: "in_progress",
            participants: [
                {
                    id: 7,
                    display_name: "Host Alex",
                    connection_status: "connected",
                    participation_status: "playing",
                    current_score: 12,
                },
                {
                    id: 9,
                    display_name: "Jamie",
                    connection_status: "connected",
                    participation_status: "playing",
                },
            ],
        }),
    });

    assert.equal(harness.elementsById.get("lobby-view").hidden, true);
    assert.equal(harness.elementsById.get("game-view").hidden, false);
    const entries = harness.elementsById.get("game-participant-list").children;
    assert.equal(entries.length, 2);
    assert.equal(entries[0].children[1].textContent, "12");
    assert.equal(entries[1].children[1].textContent, "—");
});


test("round.started and round.timer keep the timer bar numeric across updates", async () => {
    const harness = await loadRoomLobbyScript();
    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({ roomStatus: "in_progress" }),
    });

    harness.client.handleServerEvent({
        type: "round.started",
        payload: {
            round_id: 4,
            sequence_number: 2,
            duration_seconds: 90,
            masked_word: "apple",
            role: "guesser",
            drawer_participant_id: 9,
        },
    });
    harness.client.handleServerEvent({
        type: "round.timer",
        payload: {
            round_id: 4,
            remaining_seconds: 45,
        },
    });

    assert.equal(harness.elementsById.get("round-number").textContent, "Round 2");
    assert.equal(harness.elementsById.get("timer-display").textContent, "45");
    assert.equal(harness.elementsById.get("timer-bar").style.width, "50%");
    assert.equal(harness.elementsById.get("word-display").textContent, "a p p l e");
});


test("round.drawer_word restores the drawer word after reconnect", async () => {
    const harness = await loadRoomLobbyScript();
    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({ roomStatus: "in_progress" }),
    });

    harness.client.handleServerEvent({
        type: "round.drawer_word",
        payload: {
            round_id: 11,
            word: "rocket",
        },
    });

    assert.equal(harness.elementsById.get("word-display").textContent, "rocket");
    assert.equal(harness.elementsById.get("drawer-hint").hidden, false);
    assert.equal(harness.elementsById.get("guess-input").disabled, true);
});


test("spectators stay read-only during an active round", async () => {
    const harness = await loadRoomLobbyScript();
    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({
            roomStatus: "in_progress",
            participants: [
                {
                    id: 7,
                    display_name: "Host Alex",
                    connection_status: "connected",
                    participation_status: "spectating",
                },
                {
                    id: 9,
                    display_name: "Jamie",
                    connection_status: "connected",
                    participation_status: "playing",
                },
            ],
        }),
    });
    harness.client.handleServerEvent({
        type: "round.state",
        payload: {
            phase: "round",
            round_id: 8,
            drawer_participant_id: 9,
        },
    });

    assert.equal(harness.elementsById.get("guess-input").disabled, true);
    assert.equal(harness.elementsById.get("submit-guess-button").disabled, true);
    assert.equal(
        harness.elementsById.get("guess-input").placeholder,
        "You are spectating this round.",
    );
});


test("submitGuess keeps the typed text when the socket is reconnecting", async () => {
    const harness = await loadRoomLobbyScript();
    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({ roomStatus: "in_progress" }),
    });
    harness.client.handleServerEvent({
        type: "round.state",
        payload: {
            phase: "round",
            round_id: 8,
            drawer_participant_id: 9,
        },
    });

    harness.client.socket.readyState = 0;
    harness.elementsById.get("guess-input").value = "rocket";

    harness.client.submitGuess();

    assert.equal(harness.elementsById.get("guess-input").value, "rocket");
    assert.equal(
        harness.elementsById.get("lobby-error").textContent,
        "Connection is still reconnecting. Please try your guess again in a moment.",
    );
});


test("guess.error surfaces feedback instead of silently dropping the attempt", async () => {
    const harness = await loadRoomLobbyScript();

    harness.client.handleServerEvent({
        type: "guess.error",
        payload: {
            message: "No active round in progress.",
        },
    });

    assert.equal(harness.elementsById.get("lobby-error").textContent, "No active round in progress.");
    assert.equal(harness.elementsById.get("guess-history").children.length, 1);
    assert.equal(
        harness.elementsById.get("guess-history").children[0].textContent,
        "No active round in progress.",
    );
});


test("round.state intermission and game.finished populate the overlay", async () => {
    const harness = await loadRoomLobbyScript();
    harness.client.handleServerEvent({
        type: "room.state",
        payload: buildRoomState({ roomStatus: "in_progress" }),
    });

    harness.client.handleServerEvent({
        type: "round.state",
        payload: {
            phase: "intermission",
            round_id: 8,
            remaining_seconds: 6,
            leaderboard: [
                { display_name: "Host Alex", current_score: 42 },
                { display_name: "Jamie", current_score: 12 },
            ],
        },
    });

    assert.equal(harness.elementsById.get("intermission-overlay").hidden, false);
    assert.equal(harness.elementsById.get("intermission-seconds").textContent, "6");
    assert.match(harness.elementsById.get("intermission-results").innerHTML, /Leaderboard/);

    harness.client.handleServerEvent({
        type: "game.finished",
        payload: {
            winner: { display_name: "Host Alex" },
            leaderboard: [
                { display_name: "Host Alex", current_score: 42 },
                { display_name: "Jamie", current_score: 12 },
            ],
        },
    });

    assert.equal(harness.elementsById.get("intermission-title").textContent, "Game Over!");
    assert.equal(harness.intermissionTimer.hidden, true);
    assert.equal(harness.elementsById.get("intermission-return-button").hidden, false);
    assert.match(harness.elementsById.get("intermission-results").innerHTML, /Winner:/);
    assert.match(harness.elementsById.get("intermission-results").innerHTML, /Final Scores/);
});
