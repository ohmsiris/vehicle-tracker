"""Shared names for the transcribed document batches (Kaeng Khoi and Saraburi Jan-Jul 2026)."""

POLICE = "ส่วย (ตำรวจ / ตม. / ตลาด)"
MEALS = "ข้าว / น้ำ / ขนม พนักงาน"
OT = "โอทีพนักงาน"
BANK = "ธนาคาร (ค่าธรรมเนียม)"
CASH_FUEL = "น้ำมันเงินสด"
SHRINE = "ของไหว้พระ"
TYRE_FIX = "ปะยาง"
HOUSING = "ค่าห้อง / บ้านเช่า"
SUPPLIES = "ของใช้โรงงาน"
OFFICE = "เครื่องเขียน / เอกสาร"
MACHINE = "ซ่อมเครื่องจักร"
TRUCK_REPAIR = "ซ่อมรถ"
TOOLS = "เครื่องมือช่าง"
DONATION = "บริจาค / งานชุมชน"
OTHER = "อื่นๆ"
ADVANCE = "เบิกล่วงหน้าพนักงาน"
UTIL = "สาธารณูปโภค"

# Plates written on the fuel lists that are a second truck under the same number: {(number, plate fragment): plate}
PLATE = {"2087": "1ฒถ 2087", "2092": "1ฒถ 2092"}


def fuel(rows: list[tuple]) -> list[tuple]:
    """Cash-fuel list lines: (d/m, truck number, baht[, plate or note]) -> batch lines on 6130 with the truck."""
    out = []
    for r in rows:
        dm, no, amount = r[0], r[1], r[2]
        extra = r[3] if len(r) > 3 else None
        v = no or None                      # "" = a plate with no truck record: booked without a truck
        if extra and no:
            for frag, plate in PLATE.items():
                if frag in extra:
                    v = plate
        label = f"เติมน้ำมัน เบอร์ {no}" if no else "เติมน้ำมัน"
        out.append((dm, label + (f" ({extra})" if extra else ""), amount, "6130", CASH_FUEL, {"v": v} if v else {}))
    return out
