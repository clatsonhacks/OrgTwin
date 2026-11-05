import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "OrgTwin",
    short_name: "OrgTwin",
    description: "A live 3D operations twin of your company, staffed by AI agents.",
    start_url: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#a9dcff",
    theme_color: "#7cc8ff",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
