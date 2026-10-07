import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { authorize } from "./middleware/auth.ts";
import { health } from "./routes/health.ts";
import { getUser } from "./routes/users.ts";

const send = (res: any, status: number, body: unknown) => {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
};

createServer((req, res) => {
  if (!authorize(req.headers)) return send(res, 401, { error: "unauthorized" });
  const path = (req.url ?? "/").split("?")[0];
  if (req.method === "GET" && path === "/health") return send(res, 200, health());
  if (req.method === "GET" && path === "/openapi.json") return send(res, 200, JSON.parse(readFileSync(new URL("../openapi.json", import.meta.url), "utf8")));
  const user = path.match(/^\/users\/(\d+)$/);
  if (req.method === "GET" && user) {
    const result = getUser(Number(user[1]));
    return send(res, result.status, result.body);
  }
  send(res, 404, { error: "not found" });
}).listen(Number(process.env.PORT ?? 3000), "0.0.0.0");
