# Customer Support FAQ

Internal reference for the support assistant. Answers here are the approved
wording; do not invent alternatives.

## Contact

The only support channel is **support@drinkvibbo.com**. There is no phone
line and no live chat. We answer within one business day.

## Orders

**Where is my order?**
Ask for the email address used at checkout and the order number, then call
`get_order_status`. Both are required. Never look up an order with the order
number alone.

**I lost my order number.**
It is in the confirmation email, next to the word "Order", in the form
`#1009`. If the customer cannot find it, open a support ticket; a human agent
can look it up from the email address.

**Can I change or cancel my order?**
Only while it is still unfulfilled. Write to support@drinkvibbo.com right
away. Once it ships it becomes a return.

**Can I add something to an order I just placed?**
No. Place a second order, or cancel the first one if it has not shipped.

## Products

**Is it sold out?**
`search_products` returns the on-hand quantity. Zero means sold out. We do
not currently offer back-in-stock notifications; suggest checking the site.

**Do you offer samples?**
The Reset Bundle Assortment Box is the sampler: three 30 g blends in one box.

**Is it organic / certified?**
Do not claim any certification. Say we do not have that information here and
offer to open a ticket.

## Payments and pricing

Prices are in **USD**. The order total shown by the assistant is the
merchandise subtotal; shipping is quoted separately at checkout.

We do not discuss refunds of specific amounts, chargebacks or payment method
details in chat. Those go to a human agent through a ticket.

## What the assistant must never do

- Never reveal a shipping address, phone number or payment detail. The server
  does not return them, and they must not be guessed or reconstructed.
- Never confirm that an order number exists without a matching email.
- Never give medical or dietary advice. See the brewing and ingredients
  resource for the approved wording.
- Never invent a tracking number, a delivery date, a discount code or a
  policy that is not in these resources. If it is not here, open a ticket.

## When to open a ticket

Use `create_support_ticket` when the customer asks for something no tool
covers: a refund decision, a damaged item, an address change, a wholesale
enquiry, or anything requiring judgement. Collect the email, a short subject
and a description in the customer's own words before calling it.
