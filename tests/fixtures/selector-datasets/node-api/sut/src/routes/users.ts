import { Router } from "express";

export const users = Router();
users.get("/:id", (req, res) => res.json({ id: req.params.id, active: true }));
