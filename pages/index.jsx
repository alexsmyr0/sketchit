import Head from "next/head";
import { useRouter } from "next/router";
import { useState } from "react";
import styles from "../styles/EntryPage.module.css";

const LANGUAGE_OPTIONS = ["English", "Spanish", "French", "German", "Greek"];

function SketchitTitle() {
  const letters = [
    { char: "s", color: "#ff595e" },
    { char: "k", color: "#ffca3a" },
    { char: "e", color: "#8ac926" },
    { char: "t", color: "#1982c4" },
    { char: "c", color: "#6a4c93" },
    { char: "h", color: "#ff924c" },
    { char: "i", color: "#52b788" },
    { char: "t", color: "#4cc9f0" },
  ];

  return (
    <h1 className={styles.title} aria-label="sketchit">
      {letters.map((letter, index) => (
        <span key={`${letter.char}-${index}`} style={{ color: letter.color }}>
          {letter.char}
        </span>
      ))}
    </h1>
  );
}

function JoinRoomForm({
  name,
  language,
  roomCode,
  onNameChange,
  onLanguageChange,
  onRoomCodeChange,
  onPlay,
  loading,
}) {
  return (
    <form onSubmit={onPlay} className={styles.formSection}>
      <label className={styles.label} htmlFor="player-name">
        Name
      </label>
      <input
        id="player-name"
        className={styles.input}
        type="text"
        placeholder="Enter your nickname"
        value={name}
        onChange={(event) => onNameChange(event.target.value)}
      />

      <label className={styles.label} htmlFor="language">
        Language
      </label>
      <select
        id="language"
        className={styles.select}
        value={language}
        onChange={(event) => onLanguageChange(event.target.value)}
      >
        {LANGUAGE_OPTIONS.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>

      <label className={styles.label} htmlFor="room-code">
        Room Code (optional)
      </label>
      <input
        id="room-code"
        className={styles.input}
        type="text"
        placeholder="Ex: private-ab12cd"
        value={roomCode}
        onChange={(event) => onRoomCodeChange(event.target.value)}
      />

      <button className={`${styles.button} ${styles.playButton}`} type="submit" disabled={loading}>
        {loading ? "Joining..." : "Play"}
      </button>
    </form>
  );
}

function CreateRoomForm({ onCreate, loading }) {
  return (
    <div className={styles.formSection}>
      <button
        className={`${styles.button} ${styles.createButton}`}
        type="button"
        onClick={onCreate}
        disabled={loading}
      >
        {loading ? "Creating..." : "Create Private Room"}
      </button>
    </div>
  );
}

function PublicRoomList({ rooms, onJoin }) {
  return (
    <section className={styles.publicSection}>
      <h2 className={styles.sectionTitle}>Public Rooms</h2>
      {rooms.length === 0 ? (
        <p className={styles.emptyState}>No public rooms yet. Create one and start drawing.</p>
      ) : (
        <ul className={styles.roomList}>
          {rooms.map((room) => (
            <li key={room.id} className={styles.roomItem}>
              <span className={styles.roomName}>{room.name}</span>
              <button
                type="button"
                className={`${styles.button} ${styles.joinButton}`}
                onClick={() => onJoin(room.id)}
              >
                Join
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function Home({ rooms }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("English");
  const [roomCode, setRoomCode] = useState("");
  const [loadingPlay, setLoadingPlay] = useState(false);
  const [loadingCreate, setLoadingCreate] = useState(false);
  const [error, setError] = useState("");

  const normalizedName = name.trim();

  const redirectToRoom = async (id) => {
    await router.push(`/room/${encodeURIComponent(id)}`);
  };

  const handlePlay = async (event) => {
    event.preventDefault();

    if (!normalizedName) {
      setError("Please enter your name before joining.");
      return;
    }

    setError("");
    setLoadingPlay(true);

    try {
      if (roomCode.trim()) {
        await redirectToRoom(roomCode.trim());
        return;
      }

      const defaultRoomId = rooms.length > 0 ? rooms[0].id : "lobby";
      await redirectToRoom(defaultRoomId);
    } finally {
      setLoadingPlay(false);
    }
  };

  const handleCreateRoom = async () => {
    if (!normalizedName) {
      setError("Please enter your name before creating a room.");
      return;
    }

    setError("");
    setLoadingCreate(true);

    try {
      const response = await fetch("/api/rooms", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ name: normalizedName, language }),
      });

      if (!response.ok) {
        throw new Error("Unable to create room.");
      }

      const payload = await response.json();
      if (!payload?.id) {
        throw new Error("Missing room id.");
      }

      await redirectToRoom(payload.id);
    } catch {
      setError("Could not create a private room. Please try again.");
    } finally {
      setLoadingCreate(false);
    }
  };

  const handleJoinPublicRoom = async (id) => {
    if (!normalizedName) {
      setError("Please enter your name before joining.");
      return;
    }

    setError("");
    await redirectToRoom(id);
  };

  return (
    <>
      <Head>
        <title>Sketchit - Multiplayer Drawing</title>
        <meta
          name="description"
          content="Join or create a multiplayer drawing room in Sketchit."
        />
      </Head>
      <div className={styles.page}>
        <div className={styles.overlay} />
        <main className={styles.card}>
          <SketchitTitle />
          <p className={styles.subtitle}>Draw, guess, and have fun together.</p>

          <JoinRoomForm
            name={name}
            language={language}
            roomCode={roomCode}
            onNameChange={setName}
            onLanguageChange={setLanguage}
            onRoomCodeChange={setRoomCode}
            onPlay={handlePlay}
            loading={loadingPlay}
          />

          <CreateRoomForm onCreate={handleCreateRoom} loading={loadingCreate} />

          {error ? (
            <p role="alert" className={styles.errorMessage}>
              {error}
            </p>
          ) : null}

          <PublicRoomList rooms={rooms} onJoin={handleJoinPublicRoom} />
        </main>
      </div>
    </>
  );
}

export async function getServerSideProps(context) {
  const forwardedProto = context.req.headers["x-forwarded-proto"];
  const protocol = Array.isArray(forwardedProto)
    ? forwardedProto[0]
    : forwardedProto || "http";
  const host = context.req.headers.host;
  const baseUrl = `${protocol}://${host}`;

  let rooms = [];

  try {
    const response = await fetch(`${baseUrl}/api/rooms/public`);
    if (response.ok) {
      const data = await response.json();
      rooms = Array.isArray(data.rooms) ? data.rooms : [];
    }
  } catch {
    rooms = [];
  }

  return {
    props: {
      rooms,
    },
  };
}
