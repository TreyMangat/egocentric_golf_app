// BlazePose-33 skeleton topology for the SVG overlay.
//
// The connection list mirrors mediapipe.python.solutions.pose.POSE_CONNECTIONS
// at the time the backend pinned mediapipe==0.10.14. Keep in sync if the
// pose model changes (the backend's keypoints.schema field is the source of
// truth — currently "blazepose-33-v2").
//
// "v2" stores both image- and world-space arrays in the inline payload:
//   - image: 33×3 (x_norm, y_norm, visibility) — what the overlay renders.
//     BlazePose's image-space z is unreliable so we drop it.
//   - world: 33×4 (x, y, z, visibility) in metric units, hip-centered —
//     what the metrics path consumes.

export type ImageJoint = readonly [
  x: number,
  y: number,
  visibility: number,
];
export type WorldJoint = readonly [
  x: number,
  y: number,
  z: number,
  visibility: number,
];

export type ImageFrame = readonly ImageJoint[];
export type WorldFrame = readonly WorldJoint[];
export type ImageKeypointSeries = readonly ImageFrame[];
export type WorldKeypointSeries = readonly WorldFrame[];

export interface InlineKeypoints {
  image: ImageKeypointSeries;
  world?: WorldKeypointSeries;
}

export interface KeypointsRef {
  schema: string;
  fps: number;
  storageRef?: string | null;
  inline?: InlineKeypoints | null;
}

export const POSE_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  // face
  [0, 1], [1, 2], [2, 3], [3, 7],
  [0, 4], [4, 5], [5, 6], [6, 8],
  [9, 10],
  // torso
  [11, 12], [11, 23], [12, 24], [23, 24],
  // left arm
  [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  // right arm
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  // left leg
  [23, 25], [25, 27], [27, 29], [27, 31], [29, 31],
  // right leg
  [24, 26], [26, 28], [28, 30], [28, 32], [30, 32],
];

// Visibility thresholds. The lower bound matches the user spec ("draw joints
// with visibility > 0.5"); the upper bound separates "confident" joints —
// rendered in accent — from "uncertain" ones rendered in faded white. Tuned
// against MediaPipe's typical visibility distribution: a clean side-view
// swing produces > 0.85 on most joints; occluded ones drop to 0.5–0.75.
export const VIS_MIN = 0.5;
export const VIS_HIGH = 0.75;
