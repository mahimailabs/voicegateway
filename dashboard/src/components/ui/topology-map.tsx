import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch"
import { MagnifyingGlassPlus, MagnifyingGlassMinus, ArrowsOut, Pause, Play } from "@phosphor-icons/react"
import WorldMap, { type MapNode, type MapLink } from "./world-map"
import { Button } from "./button"
import { useState } from "react"

export function TopologyMap({ nodes, links }: { nodes: MapNode[]; links: MapLink[] }) {
  const [animate, setAnimate] = useState(true)
  return <div className="space-y-4">
    <TransformWrapper minScale={1} maxScale={5} wheel={{ step: 0.12 }} doubleClick={{ disabled: true }} panning={{ excluded: ["button"] }}>
      {({ zoomIn, zoomOut, resetTransform }) => <div className="relative overflow-hidden rounded-xl border bg-muted/10">
        <TransformComponent wrapperStyle={{ width: "100%", height: "clamp(300px, 48vh, 500px)" }} contentStyle={{ width: "100%", minWidth: 620 }}>
          <WorldMap nodes={nodes} links={links} animate={animate} />
        </TransformComponent>
        <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded-xl border bg-background/95 p-1 shadow-sm">
          <Button variant="ghost" size="icon-sm" aria-label="Zoom in" onClick={() => zoomIn()}><MagnifyingGlassPlus size={18} /></Button>
          <Button variant="ghost" size="icon-sm" aria-label="Zoom out" onClick={() => zoomOut()}><MagnifyingGlassMinus size={18} /></Button>
          <Button variant="ghost" size="icon-sm" aria-label="Reset map" onClick={() => resetTransform()}><ArrowsOut size={18} /></Button>
          {links.length > 0 && <Button variant="ghost" size="icon-sm" aria-label={animate ? "Pause map animation" : "Animate map"} onClick={() => setAnimate(!animate)}>{animate ? <Pause size={18} /> : <Play size={18} />}</Button>}
        </div>
      </div>}
    </TransformWrapper>
    <div aria-label="Component legend" className="flex flex-wrap gap-x-5 gap-y-2 text-xs">
      {nodes.map((node) => <span key={node.id} className="flex items-center gap-2"><span aria-hidden="true" style={{ backgroundColor: node.color }} className="size-2.5 rounded-full" />{node.label}</span>)}
    </div>
  </div>
}
