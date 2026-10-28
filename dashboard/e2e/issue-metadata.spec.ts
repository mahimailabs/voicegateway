import { test, expect } from "@playwright/test"

// The preview adapter supplies captured findings. Native HTTP tests separately prove storage,
// scope and permission checks. This exercises the approved controls over the actual API client.
test("issue decisions and archives persist through scoped HTTP writes and reload",async({page})=>{
 await page.goto("/org/default/project/default-project/issues")
 await page.getByRole("button",{name:"Issue actions",exact:true}).first().click({timeout:15000})
 const resolved=page.waitForResponse(r=>r.url().includes("/issues/")&&r.url().endsWith("/metadata")&&r.request().method()==="PUT")
 await page.getByRole("menuitem",{name:"Resolve",exact:true}).click()
 const resolution=await resolved
 expect(resolution.status()).toBe(200)
 expect(resolution.request().postDataJSON()).toMatchObject({state:"resolved",revision:0,evidenceRevision:1})
 await page.getByRole("tab",{name:/^Resolved /}).click()
 await expect(page.getByRole("button",{name:"Issue actions",exact:true}).first()).toBeVisible()
 await page.reload()
 await page.getByRole("button",{name:"Issue actions",exact:true}).first().click({timeout:15000})
 const archived=page.waitForResponse(r=>r.url()===resolution.url()&&r.request().method()==="PUT")
 await page.getByRole("menuitem",{name:"Archive",exact:true}).click()
 const archive=await archived
 expect(archive.status()).toBe(200)
 expect(archive.request().postDataJSON()).toEqual({tags:[],archived:true,revision:1,evidenceRevision:1})
 await page.getByRole("tab",{name:/^Archived /}).click()
 await page.reload()
 await page.getByRole("button",{name:"Issue actions",exact:true}).first().click({timeout:15000})
 await expect(page.getByRole("menuitem",{name:"Restore",exact:true})).toBeVisible()
})
