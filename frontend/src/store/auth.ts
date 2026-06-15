import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  OTPRequiredError,
  fetchMe,
  login as apiLogin,
  setAuthToken,
} from "@/lib/api";

/** Outcome of an attempted login. */
export type LoginOutcome =
  | { kind: "ok" }
  | { kind: "otp_required" }
  | { kind: "error"; message: string };

/** Enrolment info surfaced by the backend at step 1 when OTP is required. */
export interface OTPEnrollment {
  provisioningUri: string;
  issuer: string;
  account: string;
  secret: string;
}

interface AuthStore {
  username: string | null;
  userId: string | null;
  role: string | null;
  authEnabled: boolean | null;
  expiresAt: number | null;
  loading: boolean;
  error: string | null;
  otpRequired: boolean;
  otpEnrollment: OTPEnrollment | null;
  /** Try to log in; returns a tagged outcome instead of throwing. */
  login: (
    username: string,
    password: string,
    otpCode?: string,
  ) => Promise<LoginOutcome>;
  /** Reset the OTP-required flag (e.g. when the user goes back to step 1). */
  clearOtpRequirement: () => void;
  /** Drop the token and reset the store. */
  logout: () => void;
  /** Verify the stored token against /auth/me; used on app boot. */
  refresh: () => Promise<void>;
  /** Listen to the global 401 event raised by the axios interceptor. */
  attachUnauthorizedListener: () => void;
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set) => ({
      username: null,
      userId: null,
      role: null,
      authEnabled: null,
      expiresAt: null,
      loading: false,
      error: null,
      otpRequired: false,
      otpEnrollment: null,

      async login(username, password, otpCode) {
        set({ loading: true, error: null });
        try {
          const resp = await apiLogin(username, password, otpCode);
          setAuthToken(resp.access_token);
          set({
            username: resp.username,
            userId: resp.user_id,
            role: resp.role,
            expiresAt: Date.now() + resp.expires_in_minutes * 60_000,
            error: null,
            otpRequired: false,
            otpEnrollment: null,
          });
          // Confirm with /me so we also pick up auth_enabled flag.
          try {
            const me = await fetchMe();
            set({
              authEnabled: me.auth_enabled,
              username: me.username,
              expiresAt: me.expires_at ? me.expires_at * 1000 : null,
            });
          } catch {
            /* /me failure is non-blocking on login */
          }
          return { kind: "ok" } as const;
        } catch (e) {
          if (e instanceof OTPRequiredError) {
            set({
              otpRequired: true,
              error: null,
              otpEnrollment: e.provisioningUri
                ? {
                    provisioningUri: e.provisioningUri,
                    issuer: e.issuer ?? "",
                    account: e.account ?? username,
                    secret: e.secret ?? "",
                  }
                : null,
            });
            return { kind: "otp_required" } as const;
          }
          const message = (e as Error).message;
          set({ error: message, otpRequired: false, otpEnrollment: null });
          return { kind: "error", message } as const;
        } finally {
          set({ loading: false });
        }
      },

      clearOtpRequirement() {
        set({ otpRequired: false, error: null, otpEnrollment: null });
      },

      logout() {
        setAuthToken(null);
        set({
          username: null,
          userId: null,
          role: null,
          expiresAt: null,
          error: null,
          otpRequired: false,
          otpEnrollment: null,
        });
      },

      async refresh() {
        try {
          const me = await fetchMe();
          set({
            authEnabled: me.auth_enabled,
            username: me.authenticated ? me.username : null,
            expiresAt: me.expires_at ? me.expires_at * 1000 : null,
            error: null,
          });
        } catch {
          // Token invalid or backend down — clear local session.
          setAuthToken(null);
          set({ username: null, expiresAt: null });
        }
      },

      attachUnauthorizedListener() {
        if (typeof window === "undefined") return;
        window.addEventListener("sia:unauthorized", () => {
          set({
            username: null,
            userId: null,
            role: null,
            expiresAt: null,
          });
        });
      },
    }),
    {
      name: "sia.auth",
      partialize: (s) => ({
        username: s.username,
        userId: s.userId,
        role: s.role,
        authEnabled: s.authEnabled,
        expiresAt: s.expiresAt,
      }),
    }
  )
);
