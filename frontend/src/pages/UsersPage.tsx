import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  KeyRound,
  Loader2,
  RefreshCcw,
  Shield,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, THead, Td, Th, Tr } from "@/components/ui/table";
import {
  createUser,
  deleteUser,
  fetchUsers,
  resetUserPassword,
  updateUserRole,
} from "@/lib/api";
import type { User, UserRole } from "@/lib/types";
import { useAuthStore } from "@/store/auth";

const ROLES: UserRole[] = ["admin", "reviewer", "viewer"];

function roleLabel(role: string): string {
  switch (role) {
    case "admin":
      return "Administrateur";
    case "reviewer":
      return "Relecteur";
    case "viewer":
      return "Lecteur";
    default:
      return role;
  }
}

function roleBadgeClass(role: string): string {
  switch (role) {
    case "admin":
      return "bg-ccm-red/10 text-ccm-red border-ccm-red/20";
    case "reviewer":
      return "bg-amber-100 text-amber-700 border-amber-200";
    case "viewer":
    default:
      return "bg-slate-100 text-slate-700 border-slate-200";
  }
}

export function UsersPage() {
  const queryClient = useQueryClient();
  const currentUserId = useAuthStore((s) => s.userId);
  const currentRole = useAuthStore((s) => s.role);
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [globalSuccess, setGlobalSuccess] = useState<string | null>(null);

  const usersQuery = useQuery({
    queryKey: ["admin", "users"],
    queryFn: fetchUsers,
    staleTime: 10_000,
  });

  const invalidateUsers = () =>
    queryClient.invalidateQueries({ queryKey: ["admin", "users"] });

  const createMutation = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      invalidateUsers();
      setGlobalError(null);
      setGlobalSuccess("Utilisateur créé.");
    },
    onError: (e: Error) => {
      setGlobalError(e.message);
      setGlobalSuccess(null);
    },
  });

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: UserRole }) =>
      updateUserRole(userId, role),
    onSuccess: () => {
      invalidateUsers();
      setGlobalError(null);
      setGlobalSuccess("Rôle mis à jour.");
    },
    onError: (e: Error) => setGlobalError(e.message),
  });

  const passwordMutation = useMutation({
    mutationFn: ({ userId, password }: { userId: string; password: string }) =>
      resetUserPassword(userId, password),
    onSuccess: () => {
      setGlobalError(null);
      setGlobalSuccess("Mot de passe réinitialisé.");
    },
    onError: (e: Error) => setGlobalError(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteUser,
    onSuccess: () => {
      invalidateUsers();
      setGlobalError(null);
      setGlobalSuccess("Utilisateur supprimé.");
    },
    onError: (e: Error) => setGlobalError(e.message),
  });

  if (currentRole && currentRole !== "admin") {
    return (
      <div className="space-y-4">
        <h1 className="text-3xl font-bold text-ccm-ink">Utilisateurs</h1>
        <Alert variant="error">
          Cette page est réservée aux administrateurs.
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ccm-ink flex items-center gap-2">
            <Users className="h-7 w-7 text-ccm-red" />
            Utilisateurs
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Gestion des comptes et des rôles. Chaque action est tracée dans le
            journal d'audit.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => invalidateUsers()}
          disabled={usersQuery.isFetching}
        >
          <RefreshCcw className="h-4 w-4" />
          Rafraîchir
        </Button>
      </header>

      {globalError && <Alert variant="error">{globalError}</Alert>}
      {globalSuccess && <Alert variant="success">{globalSuccess}</Alert>}

      <CreateUserCard
        onSubmit={(input) => createMutation.mutate(input)}
        loading={createMutation.isPending}
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-ccm-red" />
            Comptes actifs
          </CardTitle>
        </CardHeader>
        <CardContent>
          {usersQuery.isLoading ? (
            <p className="text-sm text-slate-500">Chargement...</p>
          ) : usersQuery.isError ? (
            <Alert variant="error">
              Impossible de charger la liste : {(usersQuery.error as Error).message}
            </Alert>
          ) : (usersQuery.data ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">
              Aucun utilisateur enregistré.
            </p>
          ) : (
            <Table>
              <THead>
                <Tr>
                  <Th>Utilisateur</Th>
                  <Th className="w-44">Rôle</Th>
                  <Th className="w-44">Dernière connexion</Th>
                  <Th className="w-72">Actions</Th>
                </Tr>
              </THead>
              <TBody>
                {(usersQuery.data ?? []).map((u) => (
                  <UserRow
                    key={u.user_id}
                    user={u}
                    isSelf={u.user_id === currentUserId}
                    onChangeRole={(role) =>
                      roleMutation.mutate({ userId: u.user_id, role })
                    }
                    onResetPassword={(password) =>
                      passwordMutation.mutate({
                        userId: u.user_id,
                        password,
                      })
                    }
                    onDelete={() => {
                      if (
                        window.confirm(
                          `Supprimer définitivement l'utilisateur « ${u.username} » ?`,
                        )
                      ) {
                        deleteMutation.mutate(u.user_id);
                      }
                    }}
                    isMutating={
                      roleMutation.isPending ||
                      passwordMutation.isPending ||
                      deleteMutation.isPending
                    }
                  />
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CreateUserCard({
  onSubmit,
  loading,
}: {
  onSubmit: (input: { username: string; password: string; role: UserRole }) => void;
  loading: boolean;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("viewer");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || password.length < 8) return;
    onSubmit({ username: username.trim(), password, role });
    setUsername("");
    setPassword("");
    setRole("viewer");
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <UserPlus className="h-5 w-5 text-ccm-red" />
          Créer un compte
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3 sm:grid-cols-[1fr_1fr_180px_auto] items-end"
          onSubmit={submit}
        >
          <div>
            <label className="text-xs font-medium text-slate-600">
              Nom d'utilisateur
            </label>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="off"
              placeholder="prenom.nom"
              required
              minLength={1}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600">
              Mot de passe (8 caractères min.)
            </label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-600">Rôle</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              className="block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-ccm-red focus:outline-none focus:ring-1 focus:ring-ccm-red"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" disabled={loading}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            Créer
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function UserRow({
  user,
  isSelf,
  onChangeRole,
  onResetPassword,
  onDelete,
  isMutating,
}: {
  user: User;
  isSelf: boolean;
  onChangeRole: (role: UserRole) => void;
  onResetPassword: (password: string) => void;
  onDelete: () => void;
  isMutating: boolean;
}) {
  const [pwOpen, setPwOpen] = useState(false);
  const [pwValue, setPwValue] = useState("");

  return (
    <Tr>
      <Td>
        <div className="flex flex-col">
          <span className="font-medium text-ccm-ink">
            {user.username}
            {isSelf && (
              <span className="ml-2 text-[10px] uppercase tracking-wider text-slate-400">
                (vous)
              </span>
            )}
          </span>
          <span className="font-mono text-[10px] text-slate-400">
            {user.user_id}
          </span>
        </div>
      </Td>
      <Td>
        <div className="flex items-center gap-2">
          <Badge className={roleBadgeClass(user.role)}>
            {roleLabel(user.role)}
          </Badge>
          <select
            value={user.role}
            disabled={isSelf || isMutating}
            onChange={(e) => onChangeRole(e.target.value as UserRole)}
            className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </select>
        </div>
      </Td>
      <Td>
        <span className="text-xs text-slate-600">
          {user.last_login_at
            ? new Date(user.last_login_at).toLocaleString("fr-FR")
            : "Jamais"}
        </span>
      </Td>
      <Td>
        <div className="flex flex-wrap items-center gap-2">
          {pwOpen ? (
            <form
              className="flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                if (pwValue.length < 8) return;
                onResetPassword(pwValue);
                setPwValue("");
                setPwOpen(false);
              }}
            >
              <Input
                type="password"
                value={pwValue}
                onChange={(e) => setPwValue(e.target.value)}
                placeholder="Nouveau mot de passe"
                className="h-8 w-44"
                minLength={8}
                autoFocus
              />
              <Button type="submit" variant="outline" className="h-8 px-2">
                OK
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="h-8 px-2"
                onClick={() => {
                  setPwOpen(false);
                  setPwValue("");
                }}
              >
                Annuler
              </Button>
            </form>
          ) : (
            <Button
              variant="outline"
              className="h-8 px-2"
              onClick={() => setPwOpen(true)}
              disabled={isMutating}
            >
              <KeyRound className="h-3.5 w-3.5" />
              Mot de passe
            </Button>
          )}
          <Button
            variant="outline"
            className="h-8 px-2 text-red-700 border-red-200 hover:bg-red-50"
            onClick={onDelete}
            disabled={isSelf || isMutating}
            title={isSelf ? "Vous ne pouvez pas supprimer votre propre compte" : undefined}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Supprimer
          </Button>
        </div>
      </Td>
    </Tr>
  );
}
