"use client"

import { redirect } from "next/navigation"
// default page is login page
export default function HomePage() {
  redirect("/wizard")
}
