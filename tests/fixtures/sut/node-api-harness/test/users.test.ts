// Chỗ giữ cho test của app (fixture không chạy test Node; suite chặn của policy mặc định là api-contract, sast, secrets, deps).
export const cases = [{ path: "/users/1", expect: 200 }];
