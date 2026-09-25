import Link from "next/link";
import { GuestRoute } from "@/components/guest-route";
import { LoginForm } from "@/components/login-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  return (
    <GuestRoute>
      <div className="flex flex-1 items-center justify-center p-6">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>Sign in to revu</CardTitle>
            <CardDescription>Review pull requests with real cross-file context.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <LoginForm />
            <p className="text-center text-sm text-muted-foreground">
              No account?{" "}
              <Link href="/signup" className="font-medium text-foreground underline underline-offset-4">
                Sign up
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </GuestRoute>
  );
}
