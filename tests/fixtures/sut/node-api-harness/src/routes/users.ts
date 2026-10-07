export function getUser(id: number): { status: number; body: unknown } {
  if (id > 1000) return { status: 404, body: { error: "not found" } };
  return { status: 200, body: { id, active: true } };
}
