package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"mechbot/mech"
)

func main() {
	lines := make(chan []byte, 256)
	go func() {
		r := bufio.NewReaderSize(os.Stdin, 1<<20)
		for {
			line, err := r.ReadBytes('\n')
			if len(line) > 0 {
				lines <- line
			}
			if err != nil {
				close(lines)
				return
			}
		}
	}()
	out := bufio.NewWriterSize(os.Stdout, 1<<16)
	send := func(v any) {
		b, err := json.Marshal(v)
		if err != nil {
			return
		}
		out.Write(b)
		out.WriteByte('\n')
		out.Flush()
	}
	var brain *mech.Brain
	var probe struct {
		Type string `json:"type"`
	}
	for {
		first, ok := <-lines
		if !ok {
			return
		}
		batch := [][]byte{first}
	drain:
		for {
			select {
			case l, ok := <-lines:
				if !ok {
					break drain
				}
				batch = append(batch, l)
			default:
				break drain
			}
		}
		var latest []byte
		for _, raw := range batch {
			probe.Type = ""
			if json.Unmarshal(raw, &probe) != nil {
				continue
			}
			switch probe.Type {
			case "hello":
				var h mech.Hello
				if err := json.Unmarshal(raw, &h); err != nil {
					fmt.Fprintln(os.Stderr, "hello:", err)
				}
				dir := h.Map.Dir
				if dir == "" {
					dir = "/arena"
				}
				mapStart := time.Now()
				w, err := mech.LoadWorld(dir)
				if err != nil {
					fmt.Fprintln(os.Stderr, "map load failed: giddy:", err, "- playing on a flat map")
					w = mech.FlatWorld()
				} else {
					fmt.Fprintf(os.Stderr, "map loaded in %d ms: giddy nx=%d ny=%d nav_nx=%d nav_ny=%d\n", time.Since(mapStart).Milliseconds(), w.NX, w.NY, w.NNX, w.NNY)
				}
				brain = mech.NewBrain(&h, w)
				send(map[string]string{"type": "ready", "name": "giddygiddyboy"})
			case "obs":
				latest = raw
			case "end":
				return
			}
		}
		if latest == nil {
			continue
		}
		var o mech.Obs
		if err := json.Unmarshal(latest, &o); err != nil || brain == nil {
			var t struct {
				Tick int `json:"tick"`
			}
			json.Unmarshal(latest, &t)
			send(mech.Action{Tick: t.Tick})
			continue
		}
		panicsBefore := brain.Panics
		action := brain.Act(&o)
		if brain.Panics > panicsBefore {
			fmt.Fprintf(os.Stderr, "act() failed: giddy recovered panic tick=%d count=%d\n", o.Tick, brain.Panics)
		}
		send(action)
	}
}
