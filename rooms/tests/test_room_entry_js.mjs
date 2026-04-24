import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";


const ROOM_ENTRY_JS_PATH = path.resolve(
    process.cwd(),
    "rooms/static/rooms/room_entry.js",
);


function makeElement({ value = "", dataset = {} } = {}) {
    return {
        value,
        dataset,
        hidden: true,
        textContent: "",
        disabled: false,
        listeners: new Map(),
        addEventListener(eventName, listener) {
            this.listeners.set(eventName, listener);
        },
        getListener(eventName) {
            return this.listeners.get(eventName);
        },
    };
}


async function loadRoomEntryScript({
    fetchResponse,
    displayName = "Alex",
    roomCode = "",
    roomName = "",
    publicJoinCodes = [],
} = {}) {
    const source = await readFile(ROOM_ENTRY_JS_PATH, "utf8");
    const csrfInput = makeElement({ value: "csrf-token" });
    const elementsById = new Map([
        ["entry-form", makeElement()],
        ["display_name", makeElement({ value: displayName })],
        ["room_code", makeElement({ value: roomCode })],
        ["room_name", makeElement({ value: roomName })],
        ["play-button", makeElement()],
        ["create-button", makeElement()],
        ["entry-error", makeElement()],
        ["entry-status", makeElement()],
    ]);
    const publicJoinButtons = publicJoinCodes.map((joinCode) =>
        makeElement({ dataset: { joinCode } })
    );
    const redirects = [];
    const fetchCalls = [];
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
        document: {
            querySelector(selector) {
                if (selector === '[name="csrfmiddlewaretoken"]') {
                    return csrfInput;
                }
                return null;
            },
            querySelectorAll(selector) {
                if (selector === ".join-public-button") {
                    return publicJoinButtons;
                }
                return [];
            },
            getElementById(id) {
                return elementsById.get(id) ?? null;
            },
        },
        window: {
            location: {
                assign(url) {
                    redirects.push(url);
                },
            },
        },
        fetch: async (url, options) => {
            fetchCalls.push({ url, options });
            return fetchResponse(url, options);
        },
    };
    context.globalThis = context;

    vm.runInNewContext(source, context, { filename: ROOM_ENTRY_JS_PATH });

    return {
        redirects,
        fetchCalls,
        elementsById,
        publicJoinButtons,
    };
}


test("create button redirects into the existing room on recoverable conflict", async () => {
    const payload = {
        detail: "This guest session is already assigned to a room.",
        join_code: "ABC12345",
        room_url: "/rooms/ABC12345/",
    };
    const harness = await loadRoomEntryScript({
        fetchResponse: async () => ({
            ok: false,
            async json() {
                return payload;
            },
        }),
    });

    const createButton = harness.elementsById.get("create-button");
    await createButton.getListener("click")();

    assert.deepEqual(harness.redirects, [payload.room_url]);
    assert.equal(harness.elementsById.get("entry-error").hidden, true);
    assert.equal(harness.elementsById.get("entry-status").hidden, false);
    assert.equal(
        harness.elementsById.get("entry-status").textContent,
        "Creating private room...",
    );
});


test("join-by-code submit redirects into the existing room on recoverable conflict", async () => {
    const payload = {
        detail: "This guest session is already assigned to a room.",
        join_code: "ZXCV5678",
        room_url: "/rooms/ZXCV5678/",
    };
    const harness = await loadRoomEntryScript({
        roomCode: "zxcv5678",
        fetchResponse: async () => ({
            ok: false,
            async json() {
                return payload;
            },
        }),
    });

    const entryForm = harness.elementsById.get("entry-form");
    await entryForm.getListener("submit")({
        preventDefault() {},
    });

    assert.deepEqual(harness.redirects, [payload.room_url]);
    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.fetchCalls[0].url, "/rooms/zxcv5678/join/");
});


test("room-full conflicts stay on the entry page and show the error", async () => {
    const payload = {
        detail: "This room is full.",
    };
    const harness = await loadRoomEntryScript({
        publicJoinCodes: ["FULL1234"],
        fetchResponse: async () => ({
            ok: false,
            async json() {
                return payload;
            },
        }),
    });

    const joinButton = harness.publicJoinButtons[0];
    await joinButton.getListener("click")();

    assert.deepEqual(harness.redirects, []);
    assert.equal(harness.elementsById.get("entry-error").hidden, false);
    assert.equal(
        harness.elementsById.get("entry-error").textContent,
        "This room is full.",
    );
    assert.equal(harness.elementsById.get("play-button").disabled, false);
    assert.equal(harness.elementsById.get("create-button").disabled, false);
});
