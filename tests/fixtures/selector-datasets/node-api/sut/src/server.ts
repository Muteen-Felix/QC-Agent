import express from "express";
import { users } from "./routes/users";
import { legacy } from "./routes/legacy";

const app = express();
app.use("/users", users);
app.use("/legacy", legacy);
app.listen(3000);
