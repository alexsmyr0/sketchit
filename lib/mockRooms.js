const DEFAULT_ROOMS = [
  { id: "quick-draw", name: "Quick Draw", isPublic: true },
  { id: "fun-lobby", name: "Fun Lobby", isPublic: true },
  { id: "night-sketch", name: "Night Sketch", isPublic: true },
];

let rooms = [...DEFAULT_ROOMS];

export function getPublicRooms() {
  return rooms.filter((room) => room.isPublic);
}

export function createPrivateRoom(playerName = "Player") {
  const cleanedName = typeof playerName === "string" ? playerName.trim() : "";
  const ownerName = cleanedName || "Player";
  const room = {
    id: `private-${Math.random().toString(36).slice(2, 8)}`,
    name: `${ownerName}'s Room`,
    isPublic: false,
  };

  rooms = [room, ...rooms];
  return room;
}
