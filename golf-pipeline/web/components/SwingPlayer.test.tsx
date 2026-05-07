import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SwingPlayer } from "./SwingPlayer";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const frame = Array.from({ length: 33 }, () => [0.5, 0.5, 0.95] as [number, number, number]);

function renderSwingPlayer() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  let root: Root;

  act(() => {
    root = createRoot(container);
    root.render(
      <SwingPlayer
        swingId="swing_test"
        videoUrl="https://example.test/swing.mov"
        resolution={[464, 832]}
        view="DTL"
        club="driver"
        fps={60}
        keypoints={{
          schema: "blazepose-33-v2",
          fps: 60,
          inline: { image: [frame] },
        }}
        phases={null}
      />,
    );
  });

  return {
    container,
    root: root!,
    video: container.querySelector("video")!,
    svg: container.querySelector("svg")!,
  };
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("SwingPlayer overlay lifecycle", () => {
  it("draws on mount, schedules frame loops on play, and cancels them on unmount", () => {
    let nextRafId = 100;
    const requestAnimationFrame = vi
      .spyOn(window, "requestAnimationFrame")
      .mockImplementation(() => nextRafId++);
    const cancelAnimationFrame = vi
      .spyOn(window, "cancelAnimationFrame")
      .mockImplementation(() => {});

    const { root, video, svg } = renderSwingPlayer();

    expect(svg.childNodes.length).toBeGreaterThan(0);

    act(() => {
      video.dispatchEvent(new Event("play"));
    });

    expect(requestAnimationFrame).toHaveBeenCalledTimes(2);
    expect(cancelAnimationFrame).not.toHaveBeenCalled();

    act(() => {
      root.unmount();
    });

    expect(cancelAnimationFrame).toHaveBeenCalledWith(100);
    expect(cancelAnimationFrame).toHaveBeenCalledWith(101);
    expect(cancelAnimationFrame).toHaveBeenCalledTimes(2);
  });
});
