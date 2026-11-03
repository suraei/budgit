# Lens: versions and end of life
For any answer on whether a product, runtime, OS image or package supports a version, or whether a CVE affects it. Loaded by the
prompt check (R-375); the same rules ride in its routing text for chats that have no kickoff skill.
- Dates and vulnerabilities, always. An assessment that lists only support dates is incomplete: add the latest quarterly patch, the
  emergency (out-of-cycle) alerts and the patch level of the image or package that ships the version.
- Every date or figure is read at the primary source (the vendor's lifecycle page or advisory) in this session, never from memory.
- Lifecycle table: cite the exact section and row. Warn when the same version has another date in another variant (RHEL 8/9/10,
  Red Hat vs Oracle JDK, portable vs RPM or package build).
- Every cited date is visible at the link, and the text says where to look (section, row, column). A table that only a script
  fills in does not count as visible.
- A fact that depends on a contract, an entitlement or something unconfirmed is written as a condition ("if the customer has ..."),
  never as a fact.
- "Fixed", "patched", "resolved" only with an advisory the reader can open (RHSA, GHSA, USN id or a URL, or a patched version).
  Otherwise describe the mechanism and name the tracking id.
