import CallView from "@/components/CallView";

export default async function CallPage(props: PageProps<"/calls/[id]">) {
  const { id } = await props.params;
  return <CallView id={id} />;
}
