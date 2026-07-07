# Saved locations

Saved locations save a reference to a span of text for later use.  They will have one reference to a location and some metadata associated with it. 

```yaml
- id: <uuid>
    location: 6q3/12/@36+128
    date: 
    content: 
    title:
    tags: 
    note:
    sub:
      - {offset: @3+2, note: search-term, content: 寒山} 

```

The named locations should be saved to the users workspace in a new folder 'locations'. 

A selection is currently displayed on the Annot. tab of the left panel. We will add the interface for creating a named location here. The content field is optional, for very long locations (>200) only start and end are given, with ellipsis. The displayed text can be selected and the selection can create a further sub-selection with an attached note, this is repeatable. 

The filename for the location will be the <timestamp of creation>.yaml

