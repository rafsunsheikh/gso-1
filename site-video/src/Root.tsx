import React from "react";
import { Composition } from "remotion";
import { Launch } from "./Launch";

export const Root: React.FC = () => (
  <Composition id="Launch" component={Launch} durationInFrames={600} fps={30} width={1080} height={1350} />
);
