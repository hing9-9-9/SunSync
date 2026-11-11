import { buildLabel } from "./buildInfo";

/** The id is the short commit of the server's checkout, given to the build by deploy/update.sh. */
export const BUILD_LABEL = buildLabel(import.meta.env.VITE_BUILD_ID);
