# Third-party notices: the dashboard bundle

The built dashboard (`dashboard/dist`, installed into GOS_APP by `gos deploy`) includes the
packages below, unmodified, from npm. Nothing of them is vendored into this repository or into
the Python core. This file lists the packages WO-3.6's flow card added (D201).

| Package | Version | Licence | Source |
|---|---|---|---|
| elkjs | 0.10.0 | Eclipse Public License 2.0 (EPL-2.0) | https://github.com/kieler/elkjs |
| @xyflow/react | 12.8.4 | MIT | https://github.com/xyflow/xyflow |

**elkjs** is the Eclipse Layout Kernel (an Eclipse Foundation project) compiled to JavaScript,
copyright its contributors. The bundle ships it as a separate chunk, loaded only when a flow card is drawn. It is
used unmodified, and its source is at the link above, as EPL-2.0 requires. The licence text is
at https://www.eclipse.org/legal/epl-2.0/ and in the package's `LICENSE.md`.

**@xyflow/react** (React Flow) is copyright webkid GmbH, under the MIT licence. Its `LICENSE`
file in the package holds the licence text.
