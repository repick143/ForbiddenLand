import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const DEFAULT_API_PORT = "9092";

type RuntimeEnvironment = Record<string, string | undefined>;

function runtimeEnvironment(): RuntimeEnvironment {
  const globalWithProcess = globalThis as typeof globalThis & {
    process?: { env?: RuntimeEnvironment };
  };
  return globalWithProcess.process?.env ?? {};
}

function resolveApiProxyTarget(env: RuntimeEnvironment): string {
  const runtime = runtimeEnvironment();
  const explicitTarget = runtime.FORBIDDENLAND_API_PROXY_TARGET?.trim() ||
    env.FORBIDDENLAND_API_PROXY_TARGET?.trim();
  if (explicitTarget) return explicitTarget;

  const port = runtime.FORBIDDENLAND_API_PORT?.trim() ||
    env.FORBIDDENLAND_API_PORT?.trim() || DEFAULT_API_PORT;
  if (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    throw new Error(`Invalid FORBIDDENLAND_API_PORT: ${port}`);
  }
  return `http://127.0.0.1:${port}`;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: resolveApiProxyTarget(env),
          changeOrigin: true,
        },
      },
    },
  };
});
