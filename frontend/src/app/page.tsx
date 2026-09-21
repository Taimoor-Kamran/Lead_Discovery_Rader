"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { SkeletonLines } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { homeFor } from "@/lib/roles";

/** `/` only decides where to go: the role's home, or the sign-in page. */
export default function Home() {
  const { status, user } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
    if (status === "authenticated" && user) router.replace(homeFor(user.role));
  }, [status, user, router]);
  return <SkeletonLines lines={3} className="max-w-measure p-6" />;
}
