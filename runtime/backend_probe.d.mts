export declare function probeTcp(host: string, port: number, timeoutMs?: number): Promise<boolean>;
export declare function probeHttp(url: string, timeoutMs?: number): Promise<boolean>;
export declare function decideStart(input: { portUp: boolean; childAlive: boolean }):
  "already-running" | "stale-handle" | "spawn";
export declare function waitHttp(url: string, timeoutMs?: number, intervalMs?: number): Promise<boolean>;
