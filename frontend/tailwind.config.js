/** Design tokens lifted from the dashboard mockup. */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // The sidebar and hero navy. Darker than slate-900 and slightly blue,
        // so the lighthouse artwork sits on it without a visible seam.
        navy: {
          900: "#0a1222",
          850: "#0d1728",
          800: "#111d33",
          700: "#16243d",
          600: "#1d2f4d",
        },
        brand: {
          50: "#eff6ff",
          100: "#dbeafe",
          400: "#60a5fa",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
        },
        // Security grades. Fixed here so a grade is the same colour in the
        // donut, the table badge and the alert list.
        grade: {
          aplus: "#10b981",
          a: "#34d399",
          b: "#fbbf24",
          c: "#fb923c",
          d: "#ef4444",
          f: "#1e293b",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.06)",
        lift: "0 4px 12px -2px rgb(15 23 42 / 0.10), 0 2px 6px -2px rgb(15 23 42 / 0.06)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseDot: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
      },
      animation: {
        "fade-up": "fade-up .35s ease-out both",
        "pulse-dot": "pulseDot 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
