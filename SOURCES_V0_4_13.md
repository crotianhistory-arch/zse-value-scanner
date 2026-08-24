# Sources — v0.4.13 official crosswalk ingestion

Primary official source used by the first live adapter:

- United Nations Statistics Division, ISIC Rev. 4 -> ISIC Rev. 5 correspondence workbook, dated 17 January 2025:
  `https://unstats.un.org/unsd/classifications/Econ/tables/ISIC/ISIC_Rev4_to_ISIC_Rev5_Correspondence_Table-17Jan2025.xlsx`

UNSD's technical note states that the table is at the four-digit class level, includes codes/titles, GSIM change type and changed-content description, and maps every ISIC Rev. 4 class to one or more Rev. 5 classes and vice versa.

Integrity counts used by the importer:

- ISIC Rev. 4: 419 classes.
- ISIC Rev. 5: 463 classes.

These are official classification-level counts. The importer aborts if the live workbook does not cover exactly those distinct source and target class-code sets.

Future provider adapters can use the same normalized graph. U.S. Census and other national statistical agencies publish their own correspondence/concordance resources; v0.4.13 does not silently treat them as interchangeable with the UNSD source.
