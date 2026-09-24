// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Features from "./Features";

afterEach(cleanup);

it("exposes selected and required capabilities to assistive technology", () => {
  const onNext = vi.fn();
  render(<Features onNext={onNext} />);
  const chat = screen.getByRole("checkbox", { name: "Chat & LLM", checked: true });
  expect((chat as HTMLButtonElement).disabled).toBe(true);
  const voice = screen.getByRole("checkbox", { name: "Voice", checked: false });
  fireEvent.click(voice);
  expect(screen.getByRole("checkbox", { name: "Voice", checked: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(onNext).toHaveBeenLastCalledWith(expect.arrayContaining(["voice"]));
  fireEvent.click(voice);
  expect(screen.getByRole("checkbox", { name: "Voice", checked: false })).toBeTruthy();
  fireEvent.click(chat);
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(onNext).toHaveBeenLastCalledWith(expect.not.arrayContaining(["voice"]));
});

it("announces Select All and submits the same selected capabilities", () => {
  const onNext = vi.fn();
  render(<Features onNext={onNext} />);
  fireEvent.click(screen.getByRole("button", { name: "Select All" }));
  expect(screen.getAllByRole("checkbox", { checked: true })).toHaveLength(6);
  fireEvent.click(screen.getByRole("checkbox", { name: "Voice" }));
  expect(screen.getAllByRole("checkbox", { checked: true })).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", { name: "Install" }));
  expect(onNext).toHaveBeenCalledWith(expect.arrayContaining(["workflows", "rag", "image_gen"]));
  expect(onNext).toHaveBeenCalledWith(expect.not.arrayContaining(["voice"]));
  expect(screen.queryByText("&#10003;")).toBeNull();
});
