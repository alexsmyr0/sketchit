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


function buildRoomState({ roomStatus = "lobby" } = {}) {
    return {
        room: {
            name: "Sketch Room",
            join_code: "ROOM1234",
            visibility: "private",
            status: roomStatus,
        },
        host: {
            id: 7,
            display_name: "Host Alex",
        },
        participants: [
            {
                id: 7,
                display_name: "Host Alex",
                connection_status: "CONNECTED",
                participation_status: "PLAYING",
            },
            {
                id: 9,
                display_name: "Jamie",
                connection_status: "CONNECTED",
                participation_status: "PLAYING",
            },
        ],
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
} = {}) {
    const source = await readFile(ROOM_LOBBY_JS_PATH, "utf8");
    const listeners = new Map();
    const fetchCalls = [];
    const socketInstances = [];
    const timers = new Map();
    let nextTimerId = 1;

    const roomJoinCode = new MockElement({ textContent: JSON.stringify("ROOM1234") });
    const currentPlayerId = new MockElement({ textContent: JSON.stringify(7) });
    const csrfInput = new MockInputElement({ value: "csrf-token" });
    const participantList = new MockElement();
    const hostControls = new MockElement();
    hostControls.hidden = false;
    const guestView = new MockElement({ hidden: true });
    const guestViewMessage = new MockElement();
    const guestViewLoader = new MockElement();
    const saveSettingsButton = new MockButtonElement({ textContent: "Save Settings" });
    const editRoomName = new MockInputElement({ value: "Sketch Room" });
    const editVisibility = new MockSelectElement({ value: "private" });
    const startGameButton = new MockButtonElement({ textContent: "Start Game" });
    const minPlayersHint = new MockElement({ hidden: true });
    const copyUrlButton = new MockButtonElement({ textContent: "📋" });
    const joinUrlInput = new MockInputElement({ value: "http://localhost:8000/rooms/join/ROOM1234/" });
    const roomStatusBadge = new MockElement({ textContent: "lobby" });
    const settingsForm = new MockElement();
    settingsForm.elements = [editRoomName, editVisibility, saveSettingsButton];

    const elementsById = new Map([
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
        ["room-join-code", roomJoinCode],
        ["current-player-id", currentPlayerId],
    ]);

    class MockWebSocket {
        constructor(url) {
            this.url = url;
            socketInstances.push(this);
        }
    }

    const document = {
        activeElement: null,
        addEventListener(eventName, listener) {
            listeners.set(eventName, listener);
        },
        querySelector(selector) {
            if (selector === '[name="csrfmiddlewaretoken"]') {
                return csrfInput;
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
    assert.equal(harness.elementsById.get("copy-url-button").textContent, "📋");
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
