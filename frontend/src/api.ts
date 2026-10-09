import {
  createTRPCClient,
  httpBatchLink,
  httpSubscriptionLink,
  splitLink,
} from "@trpc/client";
import type { inferRouterInputs, inferRouterOutputs } from "@trpc/server";
import type { AppRouter } from "../../server/router";

export type RouterInputs = inferRouterInputs<AppRouter>;
export type RoomSnapshot = inferRouterOutputs<AppRouter>["room"];
export type Submission = inferRouterOutputs<AppRouter>["submit"];

export function sessionToken(roomId: string) {
  return sessionStorage.getItem(`leetrace:${roomId}`);
}

export function saveSession(
  session: inferRouterOutputs<AppRouter>["createRoom"],
) {
  sessionStorage.setItem(`leetrace:${session.roomId}`, session.token);
}

export const api = createTRPCClient<AppRouter>({
  links: [
    splitLink({
      condition: (operation) => operation.type === "subscription",
      true: httpSubscriptionLink({ url: "/trpc" }),
      false: httpBatchLink({
        url: "/trpc",
        headers() {
          const roomId = new URLSearchParams(window.location.search).get("id");
          const token = roomId ? sessionToken(roomId) : null;
          return token ? { authorization: `Bearer ${token}` } : {};
        },
      }),
    }),
  ],
});

export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Try again.";
}
