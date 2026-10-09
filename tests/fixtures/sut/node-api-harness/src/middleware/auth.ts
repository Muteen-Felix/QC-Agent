// Chỗ giữ cho middleware xác thực; fixture không có đăng nhập nên luôn cho qua.
export const authorize = (_headers: Record<string, unknown>): boolean => true;
