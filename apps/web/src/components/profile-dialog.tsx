"use client";

import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { displayName, formatApiError, type UserPublic } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function initials(user: UserPublic): string {
  const fromName = [user.first_name, user.last_name].map((part) => part?.trim()[0] ?? "").join("");
  return (fromName || user.email[0] || "?").toUpperCase();
}

/**
 * The signed-in person in the top bar, and where they set their name.
 * Accounts from before names were asked for show their email and a prompt
 * to add one.
 */
export function ProfileDialog({ user }: { user: UserPublic }) {
  const { updateProfile } = useAuth();
  const [open, setOpen] = useState(false);
  const [firstName, setFirstName] = useState(user.first_name ?? "");
  const [lastName, setLastName] = useState(user.last_name ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const named = Boolean(user.first_name || user.last_name);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!firstName.trim() || !lastName.trim()) {
      setError("Enter your first and last name.");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await updateProfile({ first_name: firstName.trim(), last_name: lastName.trim() });
      setOpen(false);
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setFirstName(user.first_name ?? "");
          setLastName(user.last_name ?? "");
          setError(null);
        }
      }}
    >
      <DialogTrigger
        render={<Button variant="ghost" size="sm" className="h-9 gap-2 px-1.5" aria-label="Your profile" />}
      >
        <span className="flex size-7 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
          {initials(user)}
        </span>
        <span className="hidden max-w-48 truncate text-sm sm:inline">{displayName(user)}</span>
        {!named && (
          <span className="hidden rounded-full bg-amber-500/15 px-2 py-0.5 text-xs text-amber-700 sm:inline dark:text-amber-400">
            Add your name
          </span>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Your profile</DialogTitle>
          <DialogDescription>Shown across revu instead of your email.</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="profile-first-name">First name</Label>
              <Input
                id="profile-first-name"
                autoComplete="given-name"
                maxLength={100}
                value={firstName}
                onChange={(event) => setFirstName(event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="profile-last-name">Last name</Label>
              <Input
                id="profile-last-name"
                autoComplete="family-name"
                maxLength={100}
                value={lastName}
                onChange={(event) => setLastName(event.target.value)}
              />
            </div>
          </div>
          <dl className="grid grid-cols-[5rem_1fr] gap-y-1 text-sm">
            <dt className="text-muted-foreground">Email</dt>
            <dd className="truncate">{user.email}</dd>
            <dt className="text-muted-foreground">Role</dt>
            <dd className="capitalize">{user.role}</dd>
          </dl>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="animate-spin" />}
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
