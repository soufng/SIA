/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // CCM / Moroccan flag palette
        ccm: {
          red: "#C1272D",
          "red-dark": "#8E1B22",
          "red-light": "#E0413E",
          green: "#006233",
          "green-light": "#0A7E45",
          gold: "#D4AF37",
          ink: "#1A1A1A",
          parchment: "#FAF7F2",
        },
        border: "hsl(214.3 31.8% 91.4%)",
        background: "hsl(0 0% 100%)",
        foreground: "hsl(222.2 47.4% 11.2%)",
        muted: "hsl(210 40% 96.1%)",
        "muted-foreground": "hsl(215.4 16.3% 46.9%)",
        primary: "#C1272D",
        "primary-foreground": "#FFFFFF",
        destructive: "hsl(0 84.2% 60.2%)",
        success: "hsl(142.1 76.2% 36.3%)",
        warning: "hsl(38 92% 50%)",
      },
      borderRadius: {
        lg: "0.5rem",
        md: "0.375rem",
        sm: "0.25rem",
      },
      boxShadow: {
        "ccm-soft":
          "0 1px 2px rgba(26,26,26,0.06), 0 4px 14px rgba(193,39,45,0.07)",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
