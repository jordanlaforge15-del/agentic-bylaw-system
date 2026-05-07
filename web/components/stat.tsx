import { Mono } from "./mono";

type Props = {
  n: string;
  l: string;
};

export function Stat({ n, l }: Props) {
  return (
    <div className="flex flex-col gap-1">
      <span
        className="font-sans font-bold text-[22px]"
        style={{ letterSpacing: "-0.025em" }}
      >
        {n}
      </span>
      <Mono muted size={9.5}>
        {l}
      </Mono>
    </div>
  );
}
