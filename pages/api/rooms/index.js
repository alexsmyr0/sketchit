import { createPrivateRoom } from "../../../lib/mockRooms";

export default function handler(req, res) {
  if (req.method !== "POST") {
    res.setHeader("Allow", ["POST"]);
    return res.status(405).json({ error: "Method not allowed" });
  }

  const playerName = typeof req.body?.name === "string" ? req.body.name : "Player";
  const room = createPrivateRoom(playerName);
  return res.status(201).json(room);
}
