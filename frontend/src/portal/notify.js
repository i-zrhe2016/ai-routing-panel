import { useMessage } from "naive-ui";

// useMessage() throws without an <n-message-provider> ancestor. The portal wraps
// the app in one, but isolated component tests don't — fall back to a no-op so
// views stay mountable in vitest.
export function useToast() {
  try {
    return useMessage();
  } catch (_error) {
    return { success() {}, error() {}, info() {}, warning() {} };
  }
}
