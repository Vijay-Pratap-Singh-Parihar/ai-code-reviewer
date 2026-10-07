"use client";

import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { formatApiError } from "@/lib/api-client";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * "Delete data now" with a type-to-confirm guard: deletion is permanent
 * (reviews, findings, indexes, code clones), so the button stays disabled
 * until the exact name of what is being deleted has been typed. The API
 * queues the deletion and records it in the audit log; the lists refresh
 * once it has been accepted.
 */
export function DeleteDataDialog({
  name,
  description,
  onDelete,
  triggerLabel = "Delete data now",
}: {
  name: string;
  description: ReactNode;
  onDelete: () => Promise<unknown>;
  triggerLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: onDelete,
    onSuccess: () => {
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
      void queryClient.invalidateQueries({ queryKey: ["installations"] });
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const inputId = `confirm-${name.replace(/[^a-z0-9]/gi, "-")}`;

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setTyped("");
          remove.reset();
        }
      }}
    >
      <AlertDialogTrigger render={<Button variant="destructive" size="sm" />}>
        <Trash2 />
        {triggerLabel}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete all data for {name}?</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={inputId}>
            Type <span className="font-mono font-semibold">{name}</span> to confirm
          </Label>
          <Input
            id={inputId}
            autoComplete="off"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
        </div>
        {remove.isError && (
          <p role="alert" className="text-sm text-destructive">
            {formatApiError(remove.error)}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={typed !== name || remove.isPending}
            onClick={() => remove.mutate()}
          >
            {remove.isPending ? "Deleting…" : "Delete permanently"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
