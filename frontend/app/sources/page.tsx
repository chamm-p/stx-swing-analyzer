import { redirect } from "next/navigation";

// Datenquellen sind in die Einstellungen umgezogen.
export default function SourcesRedirect() {
  redirect("/settings");
}
