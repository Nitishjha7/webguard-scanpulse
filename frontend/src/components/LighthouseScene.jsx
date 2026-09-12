/**
 * Vector stand-in for the lighthouse artwork.
 *
 * The hero prefers a real image at /assets/hero-lighthouse.jpg and falls back
 * to this, so the banner looks finished before anyone drops a file in and keeps
 * working if the asset ever 404s. Drawn rather than gradient-filled because a
 * flat gradient reads as a missing image; a scene reads as a design.
 */
export default function LighthouseScene({ className = "" }) {
  return (
    <svg
      viewBox="0 0 640 320"
      className={className}
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="lh-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1navy" />
          <stop offset="0%" stopColor="#0a1526" />
          <stop offset="55%" stopColor="#13233d" />
          <stop offset="100%" stopColor="#1b3358" />
        </linearGradient>
        <linearGradient id="lh-sea" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1b3358" />
          <stop offset="100%" stopColor="#0d1c31" />
        </linearGradient>
        <linearGradient id="lh-beam" x1="1" y1="0" x2="0" y2="0">
          <stop offset="0%" stopColor="#dbeafe" stopOpacity=".85" />
          <stop offset="60%" stopColor="#bfdbfe" stopOpacity=".18" />
          <stop offset="100%" stopColor="#bfdbfe" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="lh-glow">
          <stop offset="0%" stopColor="#fde68a" stopOpacity=".95" />
          <stop offset="100%" stopColor="#fde68a" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="lh-rock" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#1c2b45" />
          <stop offset="100%" stopColor="#080f1c" />
        </linearGradient>
      </defs>

      <rect width="640" height="320" fill="url(#lh-sky)" />

      {/* Moon and stars */}
      <circle cx="120" cy="58" r="17" fill="#cbd8ee" opacity=".9" />
      <circle cx="113" cy="53" r="15" fill="#13233d" />
      {[
        [60, 30], [190, 40], [250, 22], [330, 52], [420, 28], [500, 60], [560, 34],
        [90, 96], [280, 88], [470, 100],
      ].map(([cx, cy], index) => (
        <circle key={index} cx={cx} cy={cy} r={index % 3 ? 1 : 1.5} fill="#e2e8f0" opacity=".55" />
      ))}

      {/* Cloud bands */}
      <path d="M0 96q90-26 190-6t250-14 200 4v22q-110-18-210 2T190 112 0 122Z" fill="#1a2c4b" opacity=".55" />
      <path d="M0 132q120-22 240 0t400-10v26H0Z" fill="#182741" opacity=".5" />

      {/* Distant mountains */}
      <path d="M0 196 70 150l52 30 46-34 58 44 40-22 56 34v26H0Z" fill="#152541" />
      <path d="M120 206l60-38 44 26 52-32 60 42 48-26 66 40v22H120Z" fill="#11203a" />

      {/* Sea */}
      <rect y="212" width="640" height="108" fill="url(#lh-sea)" />
      <g stroke="#93c5fd" strokeOpacity=".22" strokeWidth="1.4" fill="none">
        <path d="M20 236h72M140 248h56M40 266h90M210 260h60M14 292h120M190 300h84" />
      </g>
      {/* Moon path on the water */}
      <path d="M104 216h18l-9 104h-8Z" fill="#cbd8ee" opacity=".14" />

      {/* Light beam, cast from the lamp toward the left */}
      <path d="M556 132 190 88l-2 66 368 24Z" fill="url(#lh-beam)" />

      {/* Headland the tower stands on */}
      <path
        d="M470 210q38-26 62-54 22 26 56 40 30 12 52 44v80H448q4-56 22-110Z"
        fill="url(#lh-rock)"
      />
      <path d="M448 262q40-18 78-6t62 40v24H448Z" fill="#060c17" />

      {/* Tower */}
      <g>
        <path d="M534 152h30l7 74h-44Z" fill="#dbe3f1" />
        <path d="M549 152h15l7 74h-22Z" fill="#aebdd5" />
        <rect x="531" y="144" width="36" height="9" rx="2" fill="#1f2f4d" />
        <rect x="536" y="120" width="26" height="24" rx="2" fill="#0f1b30" />
        <rect x="540" y="124" width="18" height="16" rx="1.5" fill="#fcd34d" />
        <path d="M535 120h28l-14-13Z" fill="#1f2f4d" />
        <path d="M549 100v8" stroke="#1f2f4d" strokeWidth="2" />
        {/* Lit windows */}
        <rect x="543" y="168" width="7" height="11" rx="1.5" fill="#fbbf24" opacity=".9" />
        <rect x="543" y="194" width="7" height="11" rx="1.5" fill="#fbbf24" opacity=".75" />
        {/* Keeper's cottage */}
        <path d="M580 208h30v20h-30Z" fill="#16233b" />
        <path d="M578 208l17-12 17 12Z" fill="#0f1b30" />
        <rect x="588" y="214" width="6" height="8" fill="#fbbf24" opacity=".8" />
        <circle cx="549" cy="132" r="30" fill="url(#lh-glow)" />
      </g>

      {/* Surf against the rocks */}
      <g fill="#bfdbfe" opacity=".3">
        <ellipse cx="452" cy="286" rx="26" ry="5" />
        <ellipse cx="410" cy="302" rx="34" ry="5" />
        <ellipse cx="498" cy="306" rx="22" ry="4" />
      </g>
    </svg>
  );
}
