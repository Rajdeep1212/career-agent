"use client";

import { useParams } from "next/navigation";
import { ApplicationView } from "../../../components/ApplicationView";

export default function ApplicationPage() {
  const { id } = useParams<{ id: string }>();
  return <ApplicationView id={decodeURIComponent(id)} />;
}
