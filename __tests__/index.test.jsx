import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";

jest.mock("next/router", () => ({
  useRouter: () => ({
    push: jest.fn(),
  }),
}));

jest.mock("../styles/EntryPage.module.css", () => new Proxy({}, { get: (target, key) => key }));

const Home = require("../pages/index").default;

describe("Sketchit entry page", () => {
  test("page renders", () => {
    render(<Home rooms={[]} />);
    expect(screen.getByRole("heading", { name: /sketchit/i })).toBeInTheDocument();
  });

  test("create room button exists", () => {
    render(<Home rooms={[]} />);
    expect(screen.getByRole("button", { name: /create private room/i })).toBeInTheDocument();
  });

  test("public rooms render", () => {
    const rooms = [
      { id: "room-1", name: "Quick Draw" },
      { id: "room-2", name: "Casual Artists" },
    ];

    render(<Home rooms={rooms} />);

    expect(screen.getByText("Quick Draw")).toBeInTheDocument();
    expect(screen.getByText("Casual Artists")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /join/i })).toHaveLength(2);
  });
});
