import { Router, type IRouter } from "express";
import healthRouter from "./health";
import fxRouter from "./fx";

const router: IRouter = Router();

router.use(healthRouter);
router.use(fxRouter);

export default router;
