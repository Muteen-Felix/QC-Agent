import { timingSafeEqual } from "crypto";

export function check(token: string, secret: string): boolean {
  const a = Buffer.from(token);
  const b = Buffer.from("sig-" + secret);
  return a.length === b.length && timingSafeEqual(a, b);
}
