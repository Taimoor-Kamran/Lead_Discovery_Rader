import { vi } from "vitest";

// `next/navigation` needs the App Router context; the pages only need a router and a path.
vi.mock("next/navigation", async () => {
  const utils = await import("./utils");
  return {
    useRouter: () => utils.router,
    usePathname: () => utils.currentPathname,
    useSearchParams: () => utils.currentSearchParams,
  };
});
