function getCsrfToken() {
    const tokenInput = document.querySelector('[name="csrfmiddlewaretoken"]');
    return tokenInput ? tokenInput.value : "";
}

function parseErrorMessage(data) {
    if (data && typeof data.detail === "string") {
        return data.detail;
    }
    if (!data || typeof data.errors !== "object") {
        return "Something went wrong. Please try again.";
    }
    return Object.entries(data.errors)
        .map(([fieldName, messages]) => {
            if (Array.isArray(messages)) {
                return `${fieldName}: ${messages.join(" ")}`;
            }
            return `${fieldName}: ${String(messages)}`;
        })
        .join(" ");
}

async function postJson(url, payload) {
    const response = await fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify(payload),
    });

    let data = {};
    try {
        data = await response.json();
    } catch (error) {
        data = {};
    }

    if (!response.ok) {
        throw new Error(parseErrorMessage(data));
    }

    return data;
}

function redirectToRoom(payload) {
    if (!payload || typeof payload.room_url !== "string") {
        throw new Error("Room URL is missing in the server response.");
    }
    window.location.assign(payload.room_url);
}

const displayNameInput = document.getElementById("display_name");
const roomCodeInput = document.getElementById("room_code");
const roomNameInput = document.getElementById("room_name");
const playButton = document.getElementById("play-button");
const createButton = document.getElementById("create-button");
const publicJoinButtons = Array.from(document.querySelectorAll(".join-public-button"));
const entryError = document.getElementById("entry-error");
const entryStatus = document.getElementById("entry-status");

function showError(message) {
    entryStatus.hidden = true;
    entryError.textContent = message;
    entryError.hidden = false;
}

function showStatus(message) {
    entryError.hidden = true;
    entryStatus.textContent = message;
    entryStatus.hidden = false;
}

function setIdleState() {
    playButton.disabled = false;
    createButton.disabled = false;
    publicJoinButtons.forEach((button) => {
        button.disabled = false;
    });
}

function setBusyState() {
    playButton.disabled = true;
    createButton.disabled = true;
    publicJoinButtons.forEach((button) => {
        button.disabled = true;
    });
}

function readDisplayName() {
    return displayNameInput.value.trim();
}

function buildPrivateRoomName(displayName) {
    const typedName = roomNameInput.value.trim();
    if (typedName) {
        return typedName;
    }
    return `${displayName}'s Room`;
}

async function joinRoomByCode(joinCode, displayName) {
    const responseData = await postJson(
        `/rooms/${encodeURIComponent(joinCode)}/join/`,
        { display_name: displayName }
    );
    redirectToRoom(responseData);
}

async function createPrivateRoom(displayName) {
    const responseData = await postJson(
        "/rooms/create/",
        {
            name: buildPrivateRoomName(displayName),
            visibility: "private",
            display_name: displayName,
        }
    );
    redirectToRoom(responseData);
}

document.getElementById("entry-form").addEventListener("submit", async function (event) {
    event.preventDefault();

    const displayName = readDisplayName();
    if (!displayName) {
        showError("Please enter your name before joining.");
        return;
    }

    setBusyState();
    showStatus("Joining room...");

    try {
        const typedRoomCode = roomCodeInput.value.trim();
        if (typedRoomCode) {
            await joinRoomByCode(typedRoomCode, displayName);
            return;
        }

        const firstPublicCode = publicJoinButtons.length > 0
            ? publicJoinButtons[0].dataset.joinCode
            : "";
        if (firstPublicCode) {
            await joinRoomByCode(firstPublicCode, displayName);
            return;
        }

        await createPrivateRoom(displayName);
    } catch (error) {
        showError(error.message);
        setIdleState();
    }
});

createButton.addEventListener("click", async function () {
    const displayName = readDisplayName();
    if (!displayName) {
        showError("Please enter your name before creating a room.");
        return;
    }

    setBusyState();
    showStatus("Creating private room...");

    try {
        await createPrivateRoom(displayName);
    } catch (error) {
        showError(error.message);
        setIdleState();
    }
});

publicJoinButtons.forEach((button) => {
    button.addEventListener("click", async function () {
        const displayName = readDisplayName();
        if (!displayName) {
            showError("Please enter your name before joining a public room.");
            return;
        }

        const joinCode = button.dataset.joinCode;
        if (!joinCode) {
            showError("This room cannot be joined right now.");
            return;
        }

        setBusyState();
        showStatus("Joining public room...");

        try {
            await joinRoomByCode(joinCode, displayName);
        } catch (error) {
            showError(error.message);
            setIdleState();
        }
    });
});
