import { useRouter } from "next/router";

export default function RoomPage() {
  const router = useRouter();
  const roomId = typeof router.query.id === "string" ? router.query.id : "";

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "#0b1938",
        color: "#ffffff",
        fontFamily: "Arial, sans-serif",
        padding: "24px",
      }}
    >
      <section
        style={{
          background: "rgba(255, 255, 255, 0.1)",
          borderRadius: "12px",
          padding: "20px 24px",
          textAlign: "center",
        }}
      >
        <h1 style={{ marginTop: 0 }}>Room: {roomId || "loading"}</h1>
        <p style={{ marginBottom: 0 }}>You entered the room successfully.</p>
      </section>
    </main>
  );
}
