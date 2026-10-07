import { Router } from "express";

export const legacy = Router();
legacy.get("/", (_req, res) => res.send("legacy"));
