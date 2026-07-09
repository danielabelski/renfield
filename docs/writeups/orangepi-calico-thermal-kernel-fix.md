# A satellite running hot: chasing 16 °C down to a missing kernel module

*How "the node is warm" turned into building netfilter modules for a vendor ARM
kernel — and why the obvious fix was the wrong one.*

---

## The setup

One of my voice-assistant **satellites** is unusual. Most are bare-metal
Raspberry Pi Zero 2 W boards, but the *Esszimmer* (dining room) satellite runs
as a **Kubernetes pod** on an **Orange Pi Zero 3W** (Allwinner **A733**, octa-core
arm64). It's a node in a small 5-node cluster:

- 1 control-plane + 3 GPU workers (x86-64)
- 1 Orange Pi worker (`orangepi-worker`), hardware-pinned to run only the
  satellite pod.

The satellite pod is **`hostNetwork: true`** — it talks to the backend over the
host's own network, so it does **not** use the cluster's pod network at all.
Hold that thought; it matters later.

The cluster CNI is **Calico**, with the default IP pool in **IPIP** mode
(`ipipMode: Always`). Kernel on the Orange Pi: a vendor build,
`6.6.98-sun60iw2`, from OrangePi's Ubuntu 22.04 image.

## The symptom

> "Esszimmer is running hot. We don't need the full capacity — can we bring the
> temperature down?"

A reasonable-sounding request with an obvious-sounding answer: cap the CPU
frequency. But *measure first.*

```
thermal_zone0 (cpub): 82.6 °C
thermal_zone3 (cpul): 82.0 °C
passive-throttle trip: 90 °C
critical trip:        110 °C
cooling_device8 (pwm-fan): cur=4 / max=4   ← fan already maxed
```

~8 °C from throttling, and **the fan was already flat out**. So passive cooling
was tapped; the only lever left was to *generate less heat*. Who was generating
it?

```
%CPU  COMMAND
63.0  calico-node     ← the k8s network daemon
18.0  python3         ← the actual satellite
13.0  systemd
12.0  dockerd
 9.4  containerd
```

The satellite — the reason this node exists — was a rounding error. **Calico was
the furnace.**

## Following the heat

### The frequency cap (a real fix, but a mask)

The governor was `ondemand` but pinned at max (1.79 GHz little / 2.0 GHz big
cluster) because of the sustained load. Capping both clusters to **1.2 GHz**:

```bash
echo 1196000 > /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq
echo 1196000 > /sys/devices/system/cpu/cpufreq/policy6/scaling_max_freq
```

82.6 °C → **~72 °C**. Persisted it as a systemd unit. A genuine ~10 °C win — but
it was treating the *symptom*. `calico-node` was still burning a whole core.
Time to find out *why*.

### Why is calico-node so busy?

```
calico-node-<orangepi>   0/1   Running   18 restarts   20d
```

`0/1` — **not ready, for 20 days**, with 18 restarts. This wasn't Calico doing
legitimate heavy networking; it was thrashing. The readiness probe told a
two-part story:

```
Number of node(s) with BGP peering established = 4     ← BGP is FINE
calico/node is not ready: felix is not ready: 503      ← Felix is not
```

So **BGP was a red herring.** The problem was Felix, Calico's dataplane agent.
And the logs showed it retrying the same thing forever:

```
felix/ipip_mgr.go 113: Failed to add IPIP tunnel device error=exit status 1
felix/ipip_mgr.go 90: Failed configure IPIP tunnel device, retrying...
```

### Red herring #1: IPIP

IPIP mode needs the `ipip` kernel module (`tunl0`). On this kernel:

```
$ modprobe ipip
modprobe: FATAL: Module ipip not found in directory /lib/modules/6.6.98-sun60iw2
$ zcat /proc/config.gz | grep IPIP
# CONFIG_NET_IPIP is not set
```

The Allwinner vendor kernel simply doesn't build IPIP. Felix could never create
the tunnel, so it retried in a tight loop — heat.

The user asked me to migrate the cluster to **VXLAN** instead. Since all 5 nodes
are on the *same subnet*, I chose **`vxlanMode: CrossSubnet`** — same-subnet
traffic routes *directly, unencapsulated* (no tunnel, no MTU change, no
pod restarts), and Calico only builds a `vxlan.calico` device that this kernel
*can* create:

```bash
kubectl patch ippool default-ipv4-ippool --type merge \
  -p '{"spec":{"ipipMode":"Never","vxlanMode":"CrossSubnet"}}'
```

All 4 x86 nodes stayed `1/1`; cross-node connectivity held. The IPIP retry loop
stopped… **and calico-node was *still* `0/1` and *still* churning.**

### The real cause: an ipset panic

Digging into Felix's health components revealed it wasn't slow — it was
*crashing*:

```
ipset v7.11: Error in line 1: Kernel error received: set type not supported
felix/ipsets.go 379: Failed to update IP sets after multiple retries.
panic
```

Felix's iptables dataplane is built on **ipsets**. And this kernel:

```
CONFIG_IP_SET=m                    ← the ipset CORE is built (and loaded)
# CONFIG_IP_SET_HASH_IP is not set  ← but NONE of the set TYPES
# CONFIG_IP_SET_HASH_NET is not set
# CONFIG_NETFILTER_XT_SET is not set ← nor the iptables '-m set' match
```

The ipset **core** was present, so `ipset` half-worked — but every *set type*
Calico needs (`hash:ip`, `hash:net`) and the iptables integration were never
compiled. Felix would create a set, the kernel would reject the type, Felix
would panic and restart, re-sync the *entire* cluster's rules, panic again.

**The verdict:** this vendor kernel fundamentally can't run Calico's iptables
dataplane. Encapsulation was never the real issue. calico-node was *never* going
to be healthy on this node — and it never had been, for 20 days.

At this point the tempting move is to shrug: keep the freq cap, exclude Calico
from this hostNetwork-only node, call it done. I proposed exactly that. The
response:

> **NO SHORTCUTS.** ChatGPT says there are kernel options. Investigate.

## The fix: build the missing modules

A missing kernel module isn't a dead end if you can *build* it. Feasibility
check first — and everything lined up:

| Requirement | Status |
|---|---|
| `CONFIG_MODVERSIONS` | **off** → no symbol-CRC matching needed |
| Module signature enforcement | **off** → unsigned modules load |
| Toolchain | gcc, make, dkms present |
| Matching kernel headers | **already on disk**: `/opt/linux-headers-current-sun60iw2_1.0.0_arm64.deb` |
| Kernel source | ipset/ipip are generic → upstream `linux-6.6.98` matches |

The headers package gave the exact `.config`, `Module.symvers`, and — crucially —
the vermagic. It was *headers-only* (0 `.c` files), so the module source came
from upstream `linux-6.6.98` (OrangePi doesn't patch generic netfilter code).

```bash
# seed the running config, enable the missing modules
zcat /proc/config.gz > .config
for o in IP_SET_HASH_IP IP_SET_HASH_NET IP_SET_HASH_IPPORT ... \
         NETFILTER_XT_SET NET_IPIP; do
  ./scripts/config --module CONFIG_$o
done

# THE detail that makes or breaks it: vermagic must match exactly.
# The running kernel's "-sun60iw2" suffix comes from a build-time LOCALVERSION,
# not the config — so bake it in, or the module refuses to load.
./scripts/config --set-str CONFIG_LOCALVERSION "-sun60iw2"
make olddefconfig
make -j8 modules_prepare
```

Three modpost gotchas, each with a clean answer:

1. **Unresolved symbols** — a fresh tree has no `Module.symvers`. Copy it from the
   installed headers tree.
2. **Core symbols still unresolved** (`_printk`, `nla_put`, `ip_tunnel_setup`) —
   the headers `Module.symvers` carries only *module* exports, not vmlinux's.
   Since MODVERSIONS is off, those resolve at *load* time against the running
   kernel — so build with **`KBUILD_MODPOST_WARN=1`** (unresolved → warning, not
   error).
3. **`make net/netfilter/ipset/` compiled `.o` but no `.ko`** — request each
   module target *explicitly*: `make ... net/netfilter/ipset/ip_set_hash_net.ko`.

Result: 18 modules, all with the **exact** matching vermagic
(`6.6.98-sun60iw2 SMP preempt mod_unload aarch64`). Install, `depmod`, and the
moment of truth:

```bash
$ modprobe ip_set_hash_net xt_set   # symbols resolve against running kernel?
$ ipset create t hash:net && ipset add t 10.0.0.0/16
hash:net OK                          # ← the exact set Calico panicked on
$ iptables -m set --match-set t src -j ACCEPT
xt_set OK
```

Then restart calico-node:

```
calico-node-<orangepi>   1/1   Running   0   45s
felix/ipsets.go 175: Queueing IP set for creation setType="hash:net"
```

**`1/1` in 45 seconds — after 20 days of crash-looping.** No panic. Felix
finally programming its dataplane like every other node.

## Results

| Metric | Before | After |
|---|---:|---:|
| CPU temperature | 82.6 °C | **66–67 °C** |
| Headroom to throttle | ~8 °C | **~24 °C** |
| calico-node readiness | 0/1 (20 days) | **1/1** |
| calico CPU | 78–88 % of a core | **~7 %** |
| Node load average | 2.3–3.4 | **0.65** |
| Cluster calico-nodes healthy | 4 / 5 | **5 / 5** |

A **16 °C** drop — and this time the heat is *gone*, not throttled away.

Made it durable: `/etc/modules-load.d`, the built `.ko`s backed up to `/opt`,
and `apt-mark hold` on the kernel so an update can't silently orphan the modules
(if the kernel *is* bumped, rebuild from the retained source tree).

## Learnings

1. **Measure before you turn the knob.** "It's hot → cap the frequency" would
   have masked a 20-day-old crash loop indefinitely. The freq cap was a fine
   *secondary* win, never the fix.

2. **The biggest consumer is rarely the thing you're thinking about.** The heat
   came from the cluster's network daemon, not the workload the node exists for.

3. **Follow the readiness message to the exact component.** "Not ready" hid two
   red herrings (BGP: fine; IPIP: real but not the whole story) before the actual
   cause — an ipset panic — surfaced. Each layer said *which* subsystem, not just
   *that* something was wrong.

4. **`ipset` core present ≠ ipset works.** A kernel can ship the framework and
   none of the types. `CONFIG_IP_SET=m` with every `CONFIG_IP_SET_HASH_*` unset
   is a silent trap for any iptables-based CNI.

5. **Vendor ARM kernels are minimal — but buildable.** Missing modules aren't a
   wall when MODVERSIONS and signing are off, the headers package is available,
   and the code is generic. The whole fix hinged on three details:
   **matching vermagic** (LOCALVERSION), **`KBUILD_MODPOST_WARN=1`** for the
   symvers gap, and **explicit `.ko` targets**.

6. **hostNetwork workloads don't need the CNI to be healthy — but a broken CNI
   still costs you.** The satellite ran fine throughout; the damage was pure
   wasted heat and CPU on a node that couldn't afford either.

7. **No shortcuts.** The workaround (exclude Calico, live with the cap) was
   available and defensible. The root fix was a kernel-module build most people
   would call out of scope. It took an afternoon and turned a chronically-sick
   node into a healthy one. Scope the hard fix *before* you settle for the mask.

---

*Environment: Calico v3.29.3, kernel 6.6.98-sun60iw2 (Allwinner A733), Kubernetes
on a mixed x86-64 / arm64 cluster. Encapsulation left on VXLAN CrossSubnet — with
the kernel fixed, IPIP would work again too, but CrossSubnet is the more
efficient choice for a single-subnet cluster.*
