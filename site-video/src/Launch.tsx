import React from "react";
import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig, Easing } from "remotion";

// GSO-1's dark theme.
const BG = "#0b0b12";
const TEXT = "#f4f4f8";
const DIM = "#9a9ab0";
const PRIMARY = "#5b5bf0";
const FONT = "Inter, -apple-system, 'SF Pro Text', Helvetica, Arial, sans-serif";

// Screenshots are 1440 x 757; shown at the frame's width.
const SHOT_W = 1080;
const SCALE = SHOT_W / 1440;
const SHOT_H = 757 * SCALE;
const SHOT_TOP = 380;

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const Shot: React.FC<{ src: string; opacity: number; y?: number; zoom?: number; origin?: string; blur?: number }> = ({ src, opacity, y = 0, zoom = 1, origin = "50% 50%", blur = 0 }) => (
  <div style={{ position: "absolute", left: 0, top: SHOT_TOP + y, width: SHOT_W, height: SHOT_H, opacity, overflow: "hidden",
    borderRadius: 18, boxShadow: "0 30px 80px rgba(0,0,0,.55)", border: "1px solid #24243a", filter: blur ? `blur(${blur}px)` : undefined }}>
    <Img src={staticFile(src)} style={{ width: "100%", transform: `scale(${zoom})`, transformOrigin: origin }} />
  </div>
);

const Caption: React.FC<{ text: string; sub?: string; opacity: number; y: number }> = ({ text, sub, opacity, y }) => (
  <div style={{ position: "absolute", left: 70, right: 70, top: 120 + y, opacity, fontFamily: FONT, color: TEXT }}>
    <div style={{ fontSize: 62, fontWeight: 700, letterSpacing: -1.2, lineHeight: 1.08 }}>{text}</div>
    {sub ? <div style={{ fontSize: 30, color: DIM, marginTop: 18 }}>{sub}</div> : null}
  </div>
);

const APPS = [
  { name: "Gmail", color: "#ea4335", from: [-200, 300] },
  { name: "iMessage", color: "#34c759", from: [1280, 260] },
  { name: "Telegram", color: "#2aabee", from: [-200, 900] },
  { name: "WhatsApp", color: "#25d366", from: [1280, 960] },
];

export const Launch: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Scene 1: the Ops Room enters.
  const enter = spring({ frame, fps, config: { damping: 18 } });
  const opsOpacity = interpolate(frame, [104, 122], [1, 0], clamp);
  // Cursor glides to the sidebar's Personal Communication > Inbox.
  const target = { x: 39 * SCALE, y: SHOT_TOP + 472 * SCALE };
  const cx = interpolate(frame, [60, 96], [900, target.x + 10], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const cy = interpolate(frame, [60, 96], [1150, target.y - 6], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const cursorOpacity = interpolate(frame, [56, 64, 108, 116], [0, 1, 1, 0], clamp);
  const ripple = interpolate(frame, [100, 118], [0, 1], clamp);

  // Scene 2: the inbox, apps flying in.
  const inboxOpacity = interpolate(frame, [110, 128, 296, 312], [0, 1, 1, 0], clamp);
  // Scene 3: the reply box, zoomed, with the draft typing itself.
  const replyOpacity = interpolate(frame, [296, 314, 412, 428], [0, 1, 1, 0], clamp);
  const zoom = interpolate(frame, [300, 340], [1, 1.9], { ...clamp, easing: Easing.out(Easing.cubic) });
  const draft = "Yes, still on! Book the Thai place, 7 works.";
  const typed = draft.slice(0, Math.max(0, Math.floor((frame - 318) / 1.9)));
  // Scene 4: the model on this Mac.
  const llmOpacity = interpolate(frame, [414, 432, 506, 522], [0, 1, 1, 0], clamp);
  const glow = 0.5 + 0.5 * Math.sin(frame / 6);
  // Scene 5: the headline.
  const endOpacity = interpolate(frame, [506, 528], [0, 1], clamp);
  const head = spring({ frame: frame - 516, fps, config: { damping: 16 } });
  const sub = interpolate(frame, [540, 556], [0, 1], clamp);

  const caption =
    frame < 110 ? { t: "Every project you own.", s: "Running, healthy, one click from started." }
    : frame < 300 ? { t: "Every conversation.", s: "Gmail, iMessage, Telegram and WhatsApp in one inbox, already sorted." }
    : frame < 420 ? { t: "Drafts in your words.", s: "Written on your Mac. Nothing is sent until you press Send." }
    : { t: "A model on your Mac.", s: "It reads, sorts and drafts. It never leaves the machine." };
  const capIn = interpolate(frame, [0, 18, 110, 124, 300, 314, 420, 434, 500, 512], [0, 1, 1, 1, 1, 1, 1, 1, 1, 0], clamp);

  return (
    <AbsoluteFill style={{ background: `radial-gradient(120% 70% at 50% 0%, #1b1b3a 0%, ${BG} 60%)`, fontFamily: FONT }}>
      <Caption text={caption.t} sub={caption.s} opacity={capIn} y={(1 - enter) * 30} />

      <Shot src="ops-room.jpg" opacity={opsOpacity * enter} y={(1 - enter) * 200} />
      <Shot src="conversation.jpg" opacity={inboxOpacity} />
      <Shot src="conversation.jpg" opacity={replyOpacity} zoom={zoom} origin="60% 92%" />
      <Shot src="local-llm.jpg" opacity={llmOpacity} />
      <Shot src="conversation.jpg" opacity={endOpacity * 0.55} blur={14} />

      {/* the tap */}
      <div style={{ position: "absolute", left: target.x - 40, top: target.y - 34, width: 110, height: 110, borderRadius: 55,
        border: `3px solid ${PRIMARY}`, opacity: (1 - ripple) * (frame >= 100 ? 1 : 0), transform: `scale(${0.3 + ripple})` }} />
      <svg style={{ position: "absolute", left: cx, top: cy, opacity: cursorOpacity }} width="34" height="44" viewBox="0 0 17 22">
        <path d="M1 1 L1 17 L5 13 L8 20 L11 19 L8 12 L14 12 Z" fill="#fff" stroke="#000" strokeWidth="1.2" />
      </svg>

      {/* the apps landing in one inbox */}
      {APPS.map((a, i) => {
        const p = spring({ frame: frame - 122 - i * 9, fps, config: { damping: 14 } });
        const land = { x: 20 * SCALE + 255 * SCALE, y: SHOT_TOP + (329 + i * 85) * SCALE };
        const x = interpolate(p, [0, 1], [a.from[0], land.x]);
        const y = interpolate(p, [0, 1], [a.from[1], land.y]);
        const show = interpolate(frame, [118, 126, 290, 300], [0, 1, 1, 0], clamp);
        return (
          <div key={a.name} style={{ position: "absolute", left: x - 12, top: y - 12, opacity: show, display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 24, height: 24, borderRadius: 12, background: a.color, boxShadow: `0 0 24px ${a.color}` }} />
            <div style={{ color: TEXT, fontSize: 24, fontWeight: 600, opacity: 1 - p * 0.9 }}>{a.name}</div>
          </div>
        );
      })}

      {/* the draft writing itself */}
      {frame >= 300 && frame < 428 ? (
        <div style={{ position: "absolute", left: 90, right: 90, top: SHOT_TOP + SHOT_H + 50, opacity: replyOpacity }}>
          <div style={{ display: "inline-block", padding: "8px 16px", borderRadius: 999, background: "#1d1d3a", border: `1px solid ${PRIMARY}`,
            color: "#c9c9ff", fontSize: 24, fontWeight: 600 }}>✦ Draft · Friendly</div>
          <div style={{ marginTop: 22, padding: "22px 26px", borderRadius: 18, background: "#15152a", border: "1px solid #2b2b4a",
            color: TEXT, fontSize: 36, lineHeight: 1.35, minHeight: 100 }}>
            {typed}<span style={{ opacity: frame % 20 < 10 ? 1 : 0, color: PRIMARY }}>▍</span>
          </div>
        </div>
      ) : null}

      {/* the model card */}
      {frame >= 414 && frame < 522 ? (
        <div style={{ position: "absolute", left: 90, right: 90, top: SHOT_TOP + SHOT_H + 60, opacity: llmOpacity,
          padding: "26px 30px", borderRadius: 20, background: "#0e2a24", border: "1px solid #1f6f5c",
          boxShadow: `0 0 ${30 + glow * 40}px rgba(37, 211, 140, ${0.25 + glow * 0.25})` }}>
          <div style={{ color: "#7ef0c4", fontSize: 22, fontWeight: 700, letterSpacing: 2 }}>● LLAMA-SERVER · ON THIS MAC</div>
          <div style={{ color: TEXT, fontSize: 40, fontWeight: 700, marginTop: 10 }}>Qwen3-VL 8B</div>
          <div style={{ color: DIM, fontSize: 26, marginTop: 6 }}>Sorts, summarises, reads photos, drafts. Nothing leaves.</div>
        </div>
      ) : null}

      {/* the headline */}
      <div style={{ position: "absolute", left: 80, right: 80, top: 470, opacity: endOpacity, transform: `translateY(${(1 - head) * 40}px)`, textAlign: "center" }}>
        <div style={{ color: TEXT, fontSize: 84, fontWeight: 800, letterSpacing: -2, lineHeight: 1.05 }}>
          Every project.<br />Every conversation.<br /><span style={{ color: "#9d9dff" }}>One staff officer.</span>
        </div>
        <div style={{ color: DIM, fontSize: 32, marginTop: 40, opacity: sub }}>GSO-1 Pro · runs on your Mac · nothing leaves it</div>
      </div>
    </AbsoluteFill>
  );
};
